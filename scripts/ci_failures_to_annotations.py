"""CI helper: turn a pytest JUnit XML report into GitHub error annotations (public, readable without login).
Usage: python scripts/ci_failures_to_annotations.py report.xml"""
import sys
import xml.etree.ElementTree as ET


def esc(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main(path: str) -> None:
    try:
        root = ET.parse(path).getroot()
    except Exception as e:  # no report: pytest did not even start
        print(f"::error title=no test report::{esc(str(e))}")
        return
    out = []
    for case in root.iter("testcase"):
        for tag in ("failure", "error"):
            el = case.find(tag)
            if el is not None:
                name = f"{case.get('classname', '')}::{case.get('name', '')}"
                detail = (el.get("message") or "") + "\n" + (el.text or "")[-1500:]
                out.append(f"### {name} [{tag}]\n{detail.strip()}")
    if not out:
        return
    # GitHub keeps 10 annotations per step: pack the failures into at most 10
    chunks = [out[i::10] for i in range(min(10, len(out)))]
    for n, chunk in enumerate(chunks, 1):
        print(f"::error title=failing tests ({n}/{len(chunks)}, {len(out)} total)::{esc(chr(10).join(chunk))[:60000]}")


if __name__ == "__main__":
    main(sys.argv[1])
