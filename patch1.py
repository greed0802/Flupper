import sys
import re

code = open('tools/probe_export.py', 'r', encoding='utf-8').read()

probe_xls_code = """
def probe_xls(data: bytes) -> dict[str, Any]:
    # Sheet names, headers, typemaps and outline status via xlrd.
    try:
        import xlrd
    except ImportError:
        return {'kind': 'xls', 'note': 'install xlrd>=2.0.1 for legacy .xls'}
    
    try:
        try:
            wb = xlrd.open_workbook(file_contents=data, formatting_info=True)
            has_format = True
        except NotImplementedError:
            wb = xlrd.open_workbook(file_contents=data)
            has_format = False
    except Exception as exc:
        return {'kind': 'xls', 'error': f'{type(exc).__name__}: {exc}'}

    sheets = []
    for ws in wb.sheets():
        header, header_row = [], None
        for r in range(min(ws.nrows, 25)):
            cells = ws.row_values(r)
            non_empty = [c for c in cells if c not in (None, '')]
            if len(non_empty) >= 2 and all(isinstance(c, str) for c in non_empty):
                header = [str(c).strip() for c in cells]
                header_row = r + 1
                break
        
        outline_levels = []
        if has_format and ws.rowinfo_map:
            levels = {info.outline_level for info in ws.rowinfo_map.values()}
            outline_levels = sorted(levels)

        col_types = {}
        type_names = {0:'EMPTY', 1:'TEXT', 2:'NUMBER', 3:'DATE', 4:'BOOL', 5:'ERR', 6:'BLANK'}
        if header_row and ws.nrows > header_row:
            from collections import Counter
            for cx in range(ws.ncols):
                types_found = Counter(ws.cell_type(rx, cx) for rx in range(header_row, min(ws.nrows, header_row+50)))
                val = {type_names.get(k, str(k)): v for k, v in types_found.items()}
                col_types[str(cx)] = val

        sheets.append({
            'name': ws.name,
            'max_row': ws.nrows,
            'max_column': ws.ncols,
            'header_row': header_row,
            'columns': header,
            'has_formatting_info': has_format,
            'rowinfo_map_size': len(ws.rowinfo_map) if has_format else 0,
            'outline_levels_found': outline_levels,
            'col_types_sample': col_types,
        })
    return {'kind': 'xls', 'sheet_count': len(sheets), 'sheets': sheets}
"""
code = code.replace('def probe_pdf(', probe_xls_code + '\n\ndef probe_pdf(')

rep1 = """                elif ext in {".xlsx", ".xlsm"}:
                    detail = probe_xlsx(data)"""
sub1 = rep1 + "\n                elif ext == \".xls\":\n                    detail = probe_xls(data)"
code = code.replace(rep1, sub1)

open('tools/probe_export.py', 'w', encoding='utf-8').write(code)
