"""Effective settings that invalidate reusable extraction results when changed."""
from importlib.metadata import PackageNotFoundError, version

def configuration_identity(tool: str) -> dict:
    packages = {'pymupdf':'PyMuPDF','camelot':'camelot-py','docling':'docling','unstructured':'unstructured'}
    try:
        installed = version(packages[tool])
    except PackageNotFoundError:
        installed = None
    if tool == 'pymupdf':
        from .pymupdf.config import STRATEGIES
        options = STRATEGIES
    elif tool == 'camelot':
        options = {'flavors':['lattice','stream','network','hybrid'],
                   'lattice_hybrid':{'copy_text':None,'shift_text':['l','t']}}
    elif tool == 'docling':
        from .docling.runner import BASELINE_CONFIGURATION
        options = BASELINE_CONFIGURATION
    else:
        from .unstructured.runner import BASELINE_CONFIGURATION
        options = BASELINE_CONFIGURATION
    return {'tool':tool,'version':installed,'options':options,'adapter_revision':2}
