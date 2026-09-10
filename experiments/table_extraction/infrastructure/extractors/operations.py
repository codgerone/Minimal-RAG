"""Tool-specific execution and serialization, supplied to the common adapter."""

def pymupdf(request, staging, *, render_table):
    from .pymupdf.runner import extract_raw_strategies
    from .pymupdf.normalizer import normalize_strategy
    from .pymupdf.export import export_strategy, export_manifest
    raw, metadata = extract_raw_strategies(request.source_pdf)
    for strategy, payload in raw.items():
        from .normalization import normalize_page_groups
        groups = {(p['page_number'],): [i] for i,p in enumerate(payload['pages']) if p['status']=='success'}
        def normalize(indices):
            i = indices[0]
            return normalize_strategy({**payload, 'pages': [{**payload['pages'][i], '_source_page_index': i}]})
        batch = normalize_page_groups(groups, normalize)
        for page in payload['pages']:
            if page['page_number'] in batch.failed_pages:
                page.update(status='failed', error=batch.failed_pages[page['page_number']])
        export_strategy(payload, batch, staging, render_table=render_table)
    export_manifest(metadata, staging)

def camelot(request, staging, *, render_table):
    from .camelot.runner import run_flavors
    from .camelot.export import export_results as export_camelot
    results = run_flavors(request.source_pdf, list(request.strategies or ('lattice','stream','network','hybrid')), request.pages)
    export_camelot(results, staging, request.source_pdf, render_table=render_table)

def docling(request, staging, *, render_table):
    from .docling.runner import run_conversion
    from .docling.export import export_results as export_docling
    export_docling(run_conversion(request.source_pdf), staging, request.source_pdf, render_table=render_table)

def unstructured(request, staging, *, render_table):
    from .unstructured.runner import run_partition
    from .unstructured.export import export_results as export_unstructured
    export_unstructured(run_partition(request.source_pdf), staging, request.source_pdf, render_table=render_table)
