from pathlib import Path
import json
import hashlib

root = Path(__file__).resolve().parent
files = sorted(root.glob("[0-9][0-9]_*.txt"))
prompts = [p for p in files if 1 <= int(p.name[:2]) <= 10]
checks = {
    "exactly_ten_prompts": len(prompts) == 10,
    "unique_role_headers": len({p.read_text(encoding="utf-8").splitlines()[0].split(":")[0] for p in prompts}) == 10,
    "exact_repository_in_every_prompt": all("https://github.com/diiaanns07-droid/Qostanay_hub.git" in p.read_text(encoding="utf-8") for p in prompts),
    "self_contained_common_and_role": all("ОБЩИЕ ИНЖЕНЕРНЫЕ ПРАВИЛА" in p.read_text(encoding="utf-8") and "ТВОЯ РОЛЬ:" in p.read_text(encoding="utf-8") for p in prompts),
    "role_matches_filename": all(p.read_text(encoding="utf-8").startswith("A"+p.name[:2]+":") for p in prompts),
    "no_bootstrap_placeholders_in_role_files": all("<ветка>" not in p.read_text(encoding="utf-8") and "<полный SHA>" not in p.read_text(encoding="utf-8") for p in prompts),
    "case_has_two_pages": json.loads((root/"CASE_PROVENANCE.json").read_text(encoding="utf-8"))["pages"] == 2,
    "source_has_case_subject": "прокторинг" in (root/"CASE_SOURCE_KK.txt").read_text(encoding="utf-8").lower(),
    "integration_followup_present": (root/"11_RETURN_TO_COORDINATOR.txt").is_file(),
    "all_prompts_bundle_contains_each_role": all(p.read_text(encoding="utf-8").split("ТВОЯ РОЛЬ:",1)[1].strip() in (root/"ALL_10_PROMPTS.txt").read_text(encoding="utf-8") for p in prompts),
}
report = {
    "checked_on": "2026-10-08",
    "scope": "Prompt package structure only; not application implementation or runtime validation",
    "checks": checks,
    "passed": all(checks.values()),
    "files": [{"name":p.name,"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"bytes":p.stat().st_size} for p in prompts],
}
(root/"CHECKS.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"passed":report["passed"],"checks":checks},ensure_ascii=False,indent=2))
raise SystemExit(0 if report["passed"] else 1)
