"""The question list of one multi-round run and its rules.

The LLM gives judgements; this module checks them and turns them into states. Code writes the facts (ids,
parents, attempts, accepted evidence, states); the LLM writes texts, notes, gaps and actions. Rules:
docs/rules/multi-round.md "问题与状态".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

Q0 = "Q0"


class Status(str, Enum):
    PENDING = "pending"       # to be retrieved in the next round
    JUDGING = "judging"       # retrieved, to be judged in the next call
    WAITING = "waiting"       # has unfinished sub-questions or chose to wait; not retrieved, judged in every call
    ANSWERED = "answered"
    ABANDONED = "abandoned"


FINISHED = (Status.ANSWERED, Status.ABANDONED)
ABANDON_REASONS = ("parent_answered", "parent_withdrawn", "withdrawn", "exhausted")
GAPS = ("not_found", "needs_basis", "needs_lead", "partial")
ACTIONS = ("wait", "retry", "derive", "withdraw", "stop")

FIRST = "first"                                            # a question's first retrieval
FALLBACK_MODES = ("continue", "rewrite", "hyde", "step_back")
Q0_ONLY = ("translation",)                                 # Q0 is retrieved without a translation the first time
WRITTEN = ("rewrite", "hyde", "step_back", "translation")  # fallback modes whose texts the LLM writes


@dataclass(frozen=True)
class Schedule:
    """Fallback groups by attempt: attempt 2 uses groups[0], attempt 3 groups[1], ... (arrangement A: one group
    with every mode; B: two groups)."""
    groups: tuple[tuple[str, ...], ...]

    def __post_init__(self):
        modes = [m for g in self.groups for m in g]
        if not self.groups or any(not g for g in self.groups) or len(set(modes)) != len(modes):
            raise ValueError("兜底安排须为非空、互不重复的分组")
        if any(m not in FALLBACK_MODES + Q0_ONLY for m in modes):
            raise ValueError(f"未知的兜底方式：{modes}")

    @property
    def max_attempts(self) -> int:
        return 1 + len(self.groups)

    def modes(self, attempt: int, question_id: str) -> tuple[str, ...]:
        """The modes of a question's attempt (1-based); Q0-only modes are left out for other questions."""
        if attempt == 1:
            return (FIRST,)
        return tuple(m for m in self.groups[attempt - 2] if question_id == Q0 or m not in Q0_ONLY)


SCHEDULE_A = Schedule((FALLBACK_MODES + Q0_ONLY,))


@dataclass(frozen=True)
class Handed:
    """One unit a mode returned: its rank in the mode's ranking, and its rank in each text's ranking (for a
    mode fused from several texts)."""
    unit_id: str
    rank: int
    ranks: tuple[tuple[int, int], ...] = ()   # (index into ModeRun.queries, rank in that text's ranking)


@dataclass
class ModeRun:
    mode: str
    queries: list[str]               # the texts retrieved (codes removed), one ranking each, fused when several
    units: list[Handed] = field(default_factory=list)   # the K units it returned
    cursor: int = 0                  # how far down the mode's ranking units have been taken


@dataclass
class Attempt:
    """One retrieval of a question: every mode its attempt number calls for."""
    round: int                       # the global retrieval round
    runs: list[ModeRun] = field(default_factory=list)


@dataclass
class Question:
    id: str
    parent_id: str | None
    text: str
    translation: dict[str, str] = field(default_factory=dict)      # document language → text
    attempts: list[Attempt] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)              # accepted unit ids, in order
    note: str = ""
    gap: str | None = None
    status: Status = Status.PENDING
    abandon_reason: str | None = None
    fallback: dict[str, list[tuple[str, str]]] = field(default_factory=dict)   # mode → (language, text), from the last retry

    @property
    def finished(self) -> bool:
        return self.status in FINISHED


# ---------------------------------------------------------------- the LLM's output

@dataclass(frozen=True)
class Judgement:
    id: str
    accept: tuple[str, ...]
    note: str
    answerable: bool
    gap: str | None = None
    action: str | None = None
    fallback: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)   # retry: mode → (language, text)
    reason: str = ""                                                                # withdraw


@dataclass(frozen=True)
class NewQuestion:
    parent_id: str
    text: str
    translation: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Withdrawal:
    """Withdrawing an unfinished question that is not judged in this call (a pending one)."""
    id: str
    reason: str


@dataclass(frozen=True)
class PlanOutput:
    judgements: tuple[Judgement, ...]
    new_questions: tuple[NewQuestion, ...] = ()
    withdrawals: tuple[Withdrawal, ...] = ()
    answer: str = ""


class QuestionList:
    """The questions of one run, in the order they were made; ids Q0, Q1, ..."""

    def __init__(self, question: str, schedule: Schedule = SCHEDULE_A):
        self.schedule = schedule
        self.questions: dict[str, Question] = {Q0: Question(Q0, None, question)}

    def __getitem__(self, qid: str) -> Question:
        return self.questions[qid]

    def __iter__(self):
        return iter(self.questions.values())

    def children(self, qid: str) -> list[Question]:
        return [q for q in self if q.parent_id == qid]

    def descendants(self, qid: str) -> list[Question]:
        out, todo = [], [qid]
        while todo:
            kids = self.children(todo.pop())
            out += kids
            todo += [k.id for k in kids]
        return out

    def has_unfinished_children(self, qid: str) -> bool:
        return any(not k.finished for k in self.children(qid))

    def to_judge(self) -> list[Question]:
        """Every unfinished question that is not waiting to be retrieved."""
        return [q for q in self if q.status in (Status.JUDGING, Status.WAITING)]

    def pending(self) -> list[Question]:
        return [q for q in self if q.status == Status.PENDING]

    def can_retry(self, qid: str) -> bool:
        return len(self[qid].attempts) < self.schedule.max_attempts

    def next_modes(self, qid: str) -> tuple[str, ...]:
        return self.schedule.modes(len(self[qid].attempts) + 1, qid)

    # ------------------------------------------------------------ checks

    def check(self, out: PlanOutput, handed: set[str], languages: list[str]) -> tuple[set[str], list[str]]:
        """The ids of the judgements that pass, and every check that fails (proposal "校验")."""
        issues: list[str] = []
        judged = {q.id for q in self.to_judge()}
        ids = [j.id for j in out.judgements]
        passed: set[str] = set()
        for qid in sorted(judged - set(ids)):
            issues.append(f"{qid}: needs a judgement")
        for qid in sorted({i for i in ids if ids.count(i) > 1}):
            issues.append(f"{qid}: has more than one judgement")
        derives = {j.id for j in out.judgements if not j.answerable and j.action == "derive"}
        for j in out.judgements:
            mine = self._check_judgement(j, judged, handed, languages)
            if j.action == "derive" and not j.answerable and not any(n.parent_id == j.id for n in out.new_questions):
                mine.append(f"{j.id}: derive needs at least one new question")
            if ids.count(j.id) == 1 and not mine:
                passed.add(j.id)
            issues += mine
        for n in out.new_questions:
            if n.parent_id not in derives:
                issues.append(f"new question \"{n.text[:40]}\": its parent {n.parent_id} does not derive in this answer")
            if not n.text.strip():
                issues.append(f"new question under {n.parent_id}: the text is empty")
            got = [l for l, _ in n.translation]
            if len(set(got)) != len(got) or any(l not in languages for l in got) or any(not t.strip() for _, t in n.translation):
                issues.append(f"new question \"{n.text[:40]}\": translations need at most one non-empty text per document language")
        for w in out.withdrawals:
            if w.id not in self.questions or self[w.id].finished or w.id == Q0 or w.id in judged:
                issues.append(f"{w.id}: only an unfinished question other than Q0 that is not judged here can be withdrawn this way")
            elif not w.reason.strip():
                issues.append(f"{w.id}: a withdrawal needs a reason")
        q0 = next((j for j in out.judgements if j.id == Q0), None)
        if q0 is not None and q0.answerable and not out.answer.strip():
            issues.append("Q0 is answerable: write the answer")
            passed.discard(Q0)
        if out.answer.strip() and not (q0 is not None and q0.answerable):
            issues.append("an answer is written only when Q0 is answerable")
        return passed, issues

    def _check_judgement(self, j: Judgement, judged: set[str], handed: set[str], languages: list[str]) -> list[str]:
        issues = []
        if j.id not in self.questions:
            return [f"{j.id}: no such question"]
        if j.id not in judged:
            return [f"{j.id}: is not to be judged in this call"]
        for u in j.accept:
            if u not in handed:
                issues.append(f"{j.id}: unit {u} has not been given to you")
        if j.answerable:
            return issues
        if j.gap not in GAPS:
            issues.append(f"{j.id}: not answerable needs a gap ({', '.join(GAPS)})")
        if j.action not in ACTIONS:
            return issues + [f"{j.id}: not answerable needs an action ({', '.join(ACTIONS)})"]
        unfinished_kids = self.has_unfinished_children(j.id)
        if j.action == "wait" and not unfinished_kids:
            issues.append(f"{j.id}: wait is only for a question with unfinished sub-questions")
        if j.action == "retry":
            if not self.can_retry(j.id):
                issues.append(f"{j.id}: has no retrieval method left; retry is not possible")
            else:
                issues += self._check_fallback(j, languages)
        if j.action == "withdraw":
            if j.id == Q0:
                issues.append("Q0 cannot be withdrawn")
            elif not j.reason.strip():
                issues.append(f"{j.id}: withdraw needs a reason")
        if j.action == "stop" and (self.can_retry(j.id) or unfinished_kids):
            issues.append(f"{j.id}: stop is only for a question with no retrieval method left and no unfinished sub-questions")
        return issues

    def _check_fallback(self, j: Judgement, languages: list[str]) -> list[str]:
        """The texts of the written modes the next attempt uses: rewrite and step-back in the question's
        language ("question") and each document language it is not written in; HyDE in each document language;
        Q0's translation at most one per document language."""
        issues = []
        for mode in self.next_modes(j.id):
            if mode not in WRITTEN:
                continue
            texts = j.fallback.get(mode, ())
            got = [l for l, _ in texts]
            if any(not t.strip() for _, t in texts) or len(set(got)) != len(got):
                issues.append(f"{j.id}: {mode} needs at most one non-empty text per language")
            elif mode in ("rewrite", "step_back"):
                if "question" not in got or any(l not in languages + ["question"] for l in got):
                    issues.append(f"{j.id}: {mode} needs a text in the question's language and one per document language it is not written in")
            elif mode == "hyde" and sorted(got) != sorted(languages):
                issues.append(f"{j.id}: hyde needs one text for each of {', '.join(languages)}")
            elif mode == "translation" and any(l not in languages for l in got):
                issues.append(f"{j.id}: translation is only into {', '.join(languages)}")
        return issues

    # ------------------------------------------------------------ applying

    def apply(self, out: PlanOutput, passed: set[str]) -> list[str]:
        """Carries out the judgements that passed and the new questions and withdrawals they bring; questions
        whose judgement did not pass keep their state. Returns the ids of the new questions."""
        made: list[str] = []
        for j in out.judgements:
            if j.id not in passed:
                continue
            q = self[j.id]
            q.evidence += [u for u in dict.fromkeys(j.accept) if u not in q.evidence]
            q.note = j.note
            if j.answerable:
                q.status, q.gap = Status.ANSWERED, None
                continue
            q.gap = j.gap
            if j.action == "wait":
                q.status = Status.WAITING
            elif j.action == "retry":
                q.status = Status.PENDING
                q.fallback = {m: list(t) for m, t in j.fallback.items()}
            elif j.action == "derive":
                q.status = Status.WAITING
                for n in out.new_questions:
                    if n.parent_id == j.id:
                        made.append(self._add(n))
            elif j.action == "withdraw":
                self._abandon(q, "withdrawn")
            elif j.action == "stop":
                self._abandon(q, "exhausted")
        for w in out.withdrawals:
            q = self.questions.get(w.id)
            if q is not None and not q.finished and q.id != Q0 and q.status == Status.PENDING:
                self._abandon(q, "withdrawn")
        for q in list(self):   # finished parents end their unfinished descendants
            if q.status == Status.ANSWERED or q.abandon_reason in ("withdrawn",):
                reason = "parent_answered" if q.status == Status.ANSWERED else "parent_withdrawn"
                for d in self.descendants(q.id):
                    if not d.finished:
                        self._abandon(d, reason)
        return made

    def _add(self, n: NewQuestion) -> str:
        qid = f"Q{len(self.questions)}"
        self.questions[qid] = Question(qid, n.parent_id, n.text.strip(), dict(n.translation))
        return qid

    @staticmethod
    def _abandon(q: Question, reason: str) -> None:
        q.status, q.abandon_reason = Status.ABANDONED, reason

    # ------------------------------------------------------------ retrieval bookkeeping

    def retrieved(self, qid: str, attempt: Attempt) -> None:
        """Records an attempt of a pending question; it is then judged in the next call."""
        q = self[qid]
        if q.status != Status.PENDING:
            raise ValueError(f"{qid} 不是待检索状态")
        q.attempts.append(attempt)
        q.status = Status.JUDGING
