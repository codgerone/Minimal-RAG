"""Regression coverage for layer boundaries, page failures and real file contracts."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import sys
from importlib.util import resolve_name

import fitz
import pytest

from experiments.table_extraction.application.extraction import execute_extraction
from experiments.table_extraction.application.models import ExtractionRequest
from experiments.table_extraction.domain.admission import decide_candidate_admissions, evaluate_slot_matches
from experiments.table_extraction.domain.models.geometry import PageGeometry
from experiments.table_extraction.domain.models.selection import CandidateView
from experiments.table_extraction.domain.models.tables import BoundingBox
from experiments.table_extraction.infrastructure.artifacts.publication import FileArtifactPublisher, publish_directories
from experiments.table_extraction.infrastructure.pdf.coordinates import from_top_left, from_pixel_space

def pdf(path, count=3):
    with fitz.open() as doc:
        for i in range(count):
            page = doc.new_page(width=200, height=200)
            page.insert_text((20, 30), f'Page {i+1} sales 123')
        doc.save(path)
    return path

def test_dependency_direction():
    base = Path('experiments/table_extraction')
    banned = {
        'domain': ('application', 'presentation', 'infrastructure', 'bootstrap'),
        'application': ('presentation', 'infrastructure', 'bootstrap'),
        'presentation': ('infrastructure', 'bootstrap'),
        'infrastructure': ('presentation', 'bootstrap'),
    }
    for layer, denied in banned.items():
        for path in (base/layer).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                imports = [node.module or ''] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names] if isinstance(node, ast.Import) else []
                if isinstance(node, ast.ImportFrom) and node.level:
                    package='.'.join(path.parent.parts)
                    imports=[resolve_name('.'*node.level+(node.module or ''),package)]
                for name in imports:
                    assert not any(name.startswith(f'experiments.table_extraction.{part}') for part in denied), (path, name)
                    if layer == 'domain':
                        assert name.split('.')[0] not in {'fitz','camelot','docling','unstructured','bs4'}

def test_geometry_tolerance_and_bad_pixel_input():
    page = PageGeometry(1,100,100,0)
    assert from_top_left(BoundingBox(-1e-6,0,20,20),page)[0].x0 == 0
    assert from_top_left(BoundingBox(-1.1e-6,0,20,20),page)[0] is None
    assert from_top_left(BoundingBox(1,1,1,2),page)[0] is None
    for points,w,h in [([],100,100), ([[0]],100,100), ([[0,0]],0,1), ([[0,0]],'bad',100), ([[float('nan'),0]],100,100)]:
        assert from_pixel_space(points,w,h,page)[0] is None
    assert from_top_left(BoundingBox(1,1,20,20),PageGeometry(1,100,100,90))[0] is None

def test_pymupdf_middle_page_failure_keeps_later_page(tmp_path, monkeypatch):
    from experiments.table_extraction.infrastructure.extractors.pymupdf.runner import extract_raw_strategies
    source = pdf(tmp_path/'sample.pdf')
    calls=[]
    def find(page, **settings):
        calls.append(page.number)
        if page.number == 1: raise RuntimeError('page broken')
        return SimpleNamespace(tables=[])
    monkeypatch.setattr(fitz.Page,'find_tables',find)
    raw, metadata = extract_raw_strategies(source)
    assert calls == [0,1,2]*3
    for result in raw.values():
        assert [p['status'] for p in result['pages']] == ['success','failed','success']
        assert result['pages'][1]['error'] == 'RuntimeError: page broken'

def test_camelot_page_failure_keeps_same_flavor_running(tmp_path, monkeypatch):
    from experiments.table_extraction.infrastructure.extractors.camelot.runner import run_flavor
    source=pdf(tmp_path/'sample.pdf');calls=[]
    def read(*args,**kwargs):
        calls.append(kwargs['pages'])
        if kwargs['pages']=='2':raise RuntimeError('broken')
        return [SimpleNamespace(page=int(kwargs['pages']))]
    monkeypatch.setitem(sys.modules,'camelot',SimpleNamespace(read_pdf=read))
    result=run_flavor(source,'lattice','all')
    assert calls==['1','2','3']
    assert [t.page for t in result.tables]==[1,3]
    assert [p['status'] for p in result.run_metadata['page_results']]==['success','failed','success']

def test_merged_text_only_deduplicates_confirmed_component():
    from experiments.table_extraction.infrastructure.extractors.camelot.normalizer import _component_text
    cells={(0,0):SimpleNamespace(text='xyz'),(0,1):SimpleNamespace(text='xyz')}
    assert _component_text(set(cells),cells,(0,0),[])=='xyz'
    assert _component_text(set(cells),cells,(0,0),[],confirmed=False)=='xyz\nxyz'
    cells[(0,0)].text='xyz xyz'
    assert _component_text({(0,0)},cells,(0,0),[])=='xyz xyz'

def test_docling_span_conflict_preserves_readable_offsets():
    from experiments.table_extraction.infrastructure.extractors.docling.normalizer import _cell_span
    cell=SimpleNamespace(start_row_offset_idx=0,end_row_offset_idx=2,start_col_offset_idx=0,
                         end_col_offset_idx=1,row_span=1,col_span=1)
    result=_cell_span(cell,0,[])
    assert result==(1,1,0,2,0,1,'unavailable')

def test_failed_slot_page_is_not_no_table_rejection():
    view=CandidateView('c','pymupdf','lines','raw',None,2,100,100,BoundingBox(0,0,20,20),'comparable',None)
    evaluations=evaluate_slot_matches([view],[],failed_pages={2})
    result=decide_candidate_admissions([view],evaluations,failed_pages={2})[0]
    assert result.processing_status=='deferred'
    assert result.deferred_reason=='slot_source_page_failed'
    assert result.admission_decision is None
    assert view.processing_status=='comparable'

def test_publication_exception_preserves_previous_output(tmp_path):
    target=tmp_path/'result';target.mkdir();(target/'old').write_text('valid')
    with pytest.raises(RuntimeError):
        with FileArtifactPublisher().stage(target) as stage:
            (stage/'new').write_text('unfinished')
            raise RuntimeError('failed')
    assert (target/'old').read_text()=='valid'
    assert not (target/'new').exists()

def test_multi_directory_publication_rolls_back(tmp_path, monkeypatch):
    staging=tmp_path/'staging';staging.mkdir();(staging/'new').touch()
    target=tmp_path/'result';target.mkdir();(target/'old').touch()
    missing=tmp_path/'missing'
    with pytest.raises(FileNotFoundError):
        publish_directories([(staging,target),(missing,tmp_path/'views')])
    assert (target/'old').exists()
    assert (staging/'new').exists()

def test_real_pymupdf_zero_tables_and_versioned_manifest(tmp_path):
    from experiments.table_extraction.bootstrap import adapters
    source=pdf(tmp_path/'empty.pdf',1)
    destination=tmp_path/'extracting'/'pymupdf'/'empty'
    result=execute_extraction(ExtractionRequest(source,destination,'pymupdf'),adapters['pymupdf'],FileArtifactPublisher())
    manifest=json.loads(result.manifest_path.read_text())
    assert manifest['format_version']=='table_extraction_v2'
    assert result.succeeded
    assert len(manifest['executions'])==3
    for strategy in ('lines','lines_strict','text'):
        assert (destination/'normalized'/strategy/'tables.json').exists()
    assert manifest['source_sha256']

def test_unstructured_missing_html_retains_unplaced_text(tmp_path,monkeypatch):
    from experiments.table_extraction.infrastructure.extractors.unstructured import normalizer
    from experiments.table_extraction.domain.scoring.reference import tokenize_candidate
    monkeypatch.setattr(normalizer,'page_geometries',lambda _: {1:PageGeometry(1,100,100,0)})
    result=normalizer.normalize_elements([{'type':'Table','text':'sales 123','metadata':{'page_number':1}}],'sample.pdf')
    candidate=result.candidates[0]
    assert candidate.cells==[] and candidate.row_count is None
    assert candidate.unplaced_text=='sales 123'
    tokens,_=tokenize_candidate(candidate)
    assert [t.normalized_token for t in tokens]==['sales','123']
    assert all(t.cell_id is None for t in tokens)


@pytest.mark.parametrize('partial', [False, True])
def test_docling_recovery_preserves_source_page_numbers(tmp_path, partial):
    from docling_core.types.doc import DoclingDocument, Size, DocItemLabel, ProvenanceItem
    from docling_core.types.doc import BoundingBox as NativeBox
    from experiments.table_extraction.infrastructure.extractors.docling.runner import convert_with_page_recovery
    source = pdf(tmp_path/'recovery.pdf')
    def document(numbers):
        doc = DoclingDocument(name='recovery')
        for number in numbers:
            doc.add_page(number, Size(width=200, height=200))
            doc.add_text(label=DocItemLabel.TEXT, text=f'page {number}',
                         prov=ProvenanceItem(page_no=number, charspan=(0,6),
                                             bbox=NativeBox(l=20,t=30,r=80,b=40)))
        return doc
    calls = []
    def convert(path, *, page_range=None, raises_on_error=False):
        calls.append(page_range)
        if page_range is None:
            if not partial:
                raise RuntimeError('full conversion failed')
            return SimpleNamespace(status='partial_success', document=document([1,2,3]),
                                   errors=[SimpleNamespace(page_no=2)])
        if page_range[0] == 2:
            raise RuntimeError('page 2 failed')
        return SimpleNamespace(status='success', document=document([page_range[0]]), errors=[])
    doc, pages, attempts, snapshots = convert_with_page_recovery(SimpleNamespace(convert=convert),source)
    assert set(doc.pages) == {1,3}
    assert [(t.text,t.prov[0].page_no) for t in doc.texts] == [('page 1',1),('page 3',3)]
    assert [p['status'] for p in pages] == ['success','failed','success']
    assert calls == ([None,(2,2)] if partial else [None,(1,1),(2,2),(3,3)])
    assert bool(snapshots) == partial


def test_unstructured_page_failure_retains_later_elements(tmp_path, monkeypatch):
    from experiments.table_extraction.infrastructure.extractors.unstructured.runner import run_partition
    calls=[]
    def partition_pdf(*, filename, **settings):
        number=int(Path(filename).stem.split('-')[-1]); calls.append(number)
        if number==2: raise RuntimeError('page failed')
        return [SimpleNamespace(metadata=SimpleNamespace(page_number=1))]
    monkeypatch.setitem(sys.modules,'unstructured.partition.pdf',SimpleNamespace(partition_pdf=partition_pdf))
    result=run_partition(pdf(tmp_path/'pages.pdf'))
    assert calls==[1,2,3]
    assert [e.metadata.page_number for e in result.elements]==[1,3]
    assert [p['status'] for p in result.run_metadata['page_results']]==['success','failed','success']


def test_normalization_page_failure_does_not_abort_following_page():
    from experiments.table_extraction.infrastructure.extractors.normalization import normalize_page_groups
    calls=[]
    def normalize(indices):
        n=indices[0];calls.append(n)
        if n==2:raise ValueError('bad table structure')
        return SimpleNamespace(candidates=[SimpleNamespace(candidate_id=str(n),regions=[SimpleNamespace(page_number=n)])],rows_by_candidate={str(n):[]})
    result=normalize_page_groups({(1,):[1],(2,):[2],(3,):[3]},normalize)
    assert calls==[1,2,3]
    assert [c.candidate_id for c in result.candidates]==['1','3']
    assert set(result.failed_pages)=={2}


def test_manifest_identity_rejects_changed_normalized_content(tmp_path):
    from experiments.table_extraction.bootstrap import adapters
    from experiments.table_extraction.infrastructure.artifacts.readiness import extraction_issue
    source=pdf(tmp_path/'source.pdf',1)
    root=tmp_path/'extracting';destination=root/'pymupdf'/source.stem
    execute_extraction(ExtractionRequest(source,destination,'pymupdf'),adapters['pymupdf'],FileArtifactPublisher())
    assert extraction_issue(root,'pymupdf',source) is None
    tables=destination/'normalized/lines/tables.json'
    tables.write_text(tables.read_text(encoding='utf-8')+' ',encoding='utf-8')
    assert extraction_issue(root,'pymupdf',source) is not None


def test_zero_table_docling_and_camelot_publish_empty_data(tmp_path):
    from docling_core.types.doc import DoclingDocument, Size
    from experiments.table_extraction.infrastructure.extractors.docling.models import DoclingRunResult
    from experiments.table_extraction.infrastructure.extractors.docling.export import export_results as export_docling
    from experiments.table_extraction.infrastructure.extractors.camelot.models import CamelotRunResult
    from experiments.table_extraction.infrastructure.extractors.camelot.export import export_results as export_camelot
    source=pdf(tmp_path/'zero.pdf',1)
    doc=DoclingDocument(name='zero');doc.add_page(1,Size(width=200,height=200))
    target=tmp_path/'docling'
    manifest=export_docling(DoclingRunResult(status='success',document=doc),target,source,render_table=lambda _: '')
    payload=json.loads(manifest.read_text(encoding='utf-8'))
    assert (target/payload['raw_document_path']).is_file()
    assert json.loads((target/payload['normalized_strategy_path']).read_text(encoding='utf-8'))['tables']==[]
    target=tmp_path/'camelot'
    manifest=export_camelot([CamelotRunResult(flavor='stream',status='success')],target,source,render_table=lambda _: '')
    payload=json.loads(manifest.read_text(encoding='utf-8'))
    assert json.loads((target/payload['runs'][0]['normalized_path']).read_text(encoding='utf-8'))['tables']==[]


def test_readiness_rejects_subset_of_requested_pdf_pages(tmp_path):
    from experiments.table_extraction.bootstrap import adapters
    from experiments.table_extraction.infrastructure.artifacts.readiness import extraction_issue
    source=pdf(tmp_path/'subset.pdf',2)
    root=tmp_path/'extracting';destination=root/'pymupdf'/source.stem
    result=execute_extraction(ExtractionRequest(source,destination,'pymupdf'),adapters['pymupdf'],FileArtifactPublisher())
    data=json.loads(result.manifest_path.read_text(encoding='utf-8'))
    data['executions'][0]['requested_page_numbers']=[1]
    result.manifest_path.write_text(json.dumps(data),encoding='utf-8')
    assert 'do not cover' in extraction_issue(root,'pymupdf',source)


def test_docs_link_to_existing_sources():
    import re
    from urllib.parse import unquote
    base=Path('experiments')
    for path in [base/'README.md',*(base/'docs').rglob('*.md')]:
        for link in re.findall(r'\]\(([^)]+)\)',path.read_text(encoding='utf-8')):
            if link.startswith(('https:','http:','#','mailto:')):continue
            target=unquote(link.split('#')[0]).strip('<>')
            destination=(path.parent/target).resolve()
            if (base/'output').resolve() in destination.parents:continue  # Regenerable artifacts.
            assert destination.exists(),(path,link)
