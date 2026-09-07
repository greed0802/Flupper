import sys
from pathlib import Path

code = open('tools/probe_export.py', 'r', encoding='utf-8').read()

probe_loose_dir_code = """
def probe_loose_dir(path: Path, redact_headers: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        'archive': f'Loose files in {path.name}',
        'archive_alias': _anon(path.name),
        'size_mb': 0.0,
        'entries': [],
    }
    ext_counter: Counter[str] = Counter()
    by_ext: dict[str, list[Path]] = defaultdict(list)
    
    files = [p for p in path.rglob('*') if p.is_file() and p.suffix.lower() not in {'.DS_Store'}]
    report['entry_count'] = len(files)
    total_size = 0
    for p in files:
        sz = p.stat().st_size
        total_size += sz
        ext = p.suffix.lower()
        ext_counter[ext] += 1
        by_ext[ext].append(p)
        
    report['size_mb'] = round(total_size / 1e6, 2)
    report['extension_histogram'] = dict(ext_counter.most_common())
    report['sample_paths'] = [p.name for p in files[:25]]
    
    interesting = SPREADSHEET_EXT | DRAWING_EXT | MUDSHARK_EXT
    for ext in [e for e in ext_counter if e in interesting]:
        for filepath in by_ext[ext][:2]:
            try:
                data = filepath.read_bytes()
            except Exception as exc:
                report['entries'].append({'name': filepath.name, 'error': str(exc)})
                continue
            
            if ext in {'.csv', '.txt'}:
                detail = probe_csv(data)
            elif ext in {'.xlsx', '.xlsm'}:
                detail = probe_xlsx(data)
            elif ext == '.xls':
                detail = probe_xls(data)
            elif ext == '.pdf':
                detail = probe_pdf(data)
            else:
                detail = probe_binary(filepath.name, data)
                
            if redact_headers:
                detail.pop('columns', None)
                for s in detail.get('sheets', []):
                    s.pop('columns', None)

            report['entries'].append({
                'name': filepath.name,
                'ext': ext,
                **detail,
            })
    return report

def main() -> int:
"""

code = code.replace('def main() -> int:', probe_loose_dir_code)

rep2 = """    targets: list[Path] = []
    for raw in args.paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            targets += sorted(p.rglob("*.zip"))
        elif p.exists():
            targets.append(p)
        else:
            print(f"  ! not found: {p}", file=sys.stderr)

    if not targets:
        print("No archives found.", file=sys.stderr)
        return 1

    reports = []
    for t in targets:
        print(f"probing {t.name} ...", file=sys.stderr)
        reports.append(probe_archive(t, redact_headers=args.redact_headers))"""

sub2 = """    targets: list[Path] = []
    dir_targets: list[Path] = []
    for raw in args.paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            targets += sorted(p.rglob("*.zip"))
            dir_targets.append(p)
        elif p.exists():
            targets.append(p)
        else:
            print(f"  ! not found: {p}", file=sys.stderr)

    if not targets and not dir_targets:
        print("No paths found.", file=sys.stderr)
        return 1

    reports = []
    for t in targets:
        print(f"probing {t.name} (zip) ...", file=sys.stderr)
        reports.append(probe_archive(t, redact_headers=args.redact_headers))
    for d in dir_targets:
        print(f"probing {d.name} (loose dir) ...", file=sys.stderr)
        reports.append(probe_loose_dir(d, redact_headers=args.redact_headers))"""

code = code.replace(rep2, sub2)

open('tools/probe_export.py', 'w', encoding='utf-8').write(code)
