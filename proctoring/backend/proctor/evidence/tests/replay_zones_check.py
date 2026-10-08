"""Opt-in real REPLAY/API/report check. Requires the A01 integration and local media/models.

Run from proctoring: python -m proctor.evidence.tests.replay_zones_check --out <local-dir>
Writes local reports without retained camera snapshots; no media is copied into Git.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import secrets
import time

from fastapi.testclient import TestClient

from proctor.app import create_app
from proctor.evidence.review_zones import ZONE_LABEL, ReviewZone
from proctor.settings import PROCTORING_ROOT, Settings

CASES = [('zone_a_green_01', 'green'), ('zone_b_yellow_02', 'yellow'), ('zone_c_red_01', 'red')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--replay-dir', type=Path, default=Path(os.environ['LOCALAPPDATA']) / 'QorgauExam/replay')
    parser.add_argument('--models-dir', type=Path, default=Path(os.environ['LOCALAPPDATA']) / 'QorgauExam/models')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)  # do not overwrite a previous evidence run
    settings = Settings(data_dir=args.out / 'data', models_dir=args.models_dir, replay_dir=args.replay_dir,
                        exam_path=PROCTORING_ROOT / 'contracts/fixtures/v1/ExamDefinition.demo_min.json')
    token = secrets.token_hex(32)
    results = []
    app = create_app(settings, token)
    with TestClient(app, base_url='http://127.0.0.1', headers={'Authorization': f'Bearer {token}'}) as client:
        for replay_id, expected in CASES:
            manifest_bytes = (args.replay_dir / f'{replay_id}.json').read_bytes()
            manifest = json.loads(manifest_bytes)
            print(f'START {replay_id}: expected={expected}, pacing={manifest["pacing"]}', flush=True)
            response = client.post('/v1/sessions', json={
                'source': {'mode': 'replay', 'replay_id': replay_id}, 'exam_id': 'demo-exam-1',
                'student_label': replay_id, 'retain_media': False,
                'consent': {'accepted': True, 'text_version': 'consent-ru-1', 'accepted_at': '2026-10-08T09:00:00Z'},
            })
            assert response.status_code == 201, response.text
            sid = response.json()['session_id']
            preflight = client.post(f'/v1/sessions/{sid}/preflight')
            assert preflight.status_code == 200 and preflight.json()['ready'], preflight.text
            assert client.post(f'/v1/sessions/{sid}/calibration/skip', json={'reason': 'REPLAY verification'}).status_code == 200
            started = client.post(f'/v1/sessions/{sid}/start')
            assert started.status_code == 200 and started.json()['state'] == 'running', started.text
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                health = client.get('/v1/health').json()
                capture = next(c for c in health['components'] if c['component'] == 'capture')
                if capture['code'] == 'replay_ended':
                    break
                time.sleep(0.1)
            else:
                raise AssertionError(f'{replay_id}: replay did not end: {capture}')
            metrics = client.get(f'/v1/sessions/{sid}/metrics').json()
            finished = client.post(f'/v1/sessions/{sid}/finish')
            assert finished.status_code == 200 and finished.json()['state'] == 'finished', finished.text
            summary_response = client.get(f'/v1/sessions/{sid}/summary')
            overview_response = client.get('/v1/sessions/overview')
            html_response = client.get(f'/v1/sessions/{sid}/report.html')
            json_response = client.get(f'/v1/sessions/{sid}/report.json')
            for response in (summary_response, overview_response, html_response, json_response):
                assert response.status_code == 200, response.text[:500]
            summary, exported = summary_response.json(), json_response.json()
            row = next(r for r in overview_response.json() if r['session']['session_id'] == sid)
            page = html_response.text
            (args.out / f'{replay_id}.html').write_text(page, encoding='utf-8')
            (args.out / f'{replay_id}.json').write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding='utf-8')
            actual = summary['review_zone']
            assert row['review_zone'] == exported['summary']['review_zone'] == actual
            assert row['reasons_ru'] == summary['review_zone_reasons_ru'] == exported['summary']['review_zone_reasons_ru']
            assert summary['review_zone_rule_version'] == 'zone-rule-1'
            assert summary['session']['source_mode'] == row['session']['source_mode'] == 'replay'
            assert f'zone-{actual}' in page.split('</style>', 1)[1] and ZONE_LABEL[ReviewZone(actual)] in page
            assert 'REPLAY — ВОСПРОИЗВЕДЕНИЕ ЗАПИСИ' in page and 'Решения преподавателя' in page
            assert len(row['reasons_ru']) <= 3
            assert all(html.escape(reason, quote=True) in page for reason in row['reasons_ru'])
            result = dict(replay_id=replay_id, expected=expected, actual=actual,
                          outcome='PASS' if actual == expected else 'FAIL', pacing=manifest['pacing'],
                          manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                          media_sha256=manifest['media']['sha256'], observed_ms=summary['observed_ms'],
                          paused_ms=summary['paused_ms'], started_at=summary['session']['started_at'],
                          finished_at=summary['session']['finished_at'],
                          rule_version=summary['review_zone_rule_version'], reasons_ru=row['reasons_ru'],
                          incidents_total=row['incidents_total'], incidents_by_priority=row['incidents_by_priority'],
                          pending_reviews=row['pending_reviews'], overview_http=overview_response.status_code,
                          html_http=html_response.status_code, frames_captured=metrics['frames_captured'],
                          frames_dropped=metrics['frames_dropped'], consumers=metrics['consumers'],
                          incidents=[{k: d['incident'][k] for k in ('rule_id','priority','t_start_ms','t_end_ms')}
                                     for d in exported['incidents']])
            results.append(result)
            (args.out / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False), flush=True)
        overview = client.get('/v1/sessions/overview').json()
        (args.out / 'overview.json').write_text(json.dumps(overview, ensure_ascii=False, indent=2), encoding='utf-8')
        assert [r['review_zone'] for r in overview] == sorted(
            [r['review_zone'] for r in overview], key=['red', 'yellow', 'grey', 'green'].index)
    return 0 if all(r['outcome'] == 'PASS' for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
