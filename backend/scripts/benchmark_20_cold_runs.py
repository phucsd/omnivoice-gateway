#!/usr/bin/env python3
"""
OMNIVOICE GATEWAY — 20 INDEPENDENT COLD-START & MULTI-SCENARIO TTS BENCHMARK
----------------------------------------------------------------------------
Executes 20 truly independent Cold-Start runs against https://voice.oloka.net:
- Before Run #1 and immediately after EVERY run, calls POST /v1/settings/reset-workers
  to delete disposable Kaggle GPU worker kernels (omnivoice-worker-1 / omnivoice-worker-2)
  and clear Gateway WorkerSession state so every single run boots a fresh GPU worker from zero.
- Covers 5 realistic scenarios × 4 independent cycles = 20 runs:
  1. Clone Voice (Ngắn ~15 từ)
  2. Clone Voice (Vừa ~42 từ, kèm số liệu chuẩn hóa)
  3. Clone Voice (Dài ~80 từ)
  4. Voice Design (Vừa ~38 từ, đa dạng instruct)
  5. Auto Voice (Ngắn-Vừa ~26 từ)
"""

import argparse
import json
import math
import os
import statistics
import sys
import time
import wave
from datetime import datetime
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SCENARIOS = [
    # Cycle 1 (Runs 1-5)
    {
        "run_no": 1,
        "scenario_group": "Clone Voice - Ngắn",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Chào mừng các bạn quay trở lại với bản tin công nghệ sáng nay của chúng tôi.",
    },
    {
        "run_no": 2,
        "scenario_group": "Clone Voice - Vừa (Số liệu)",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Báo cáo quý 3 năm 2026 ghi nhận doanh thu đạt 1.650 tỷ đồng, tăng trưởng 18,4% so với cùng kỳ năm ngoái nhờ ứng dụng trí tuệ nhân tạo vào vận hành.",
    },
    {
        "run_no": 3,
        "scenario_group": "Clone Voice - Dài",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Giữa nhịp sống hối hả của thành phố hiện đại, đôi khi chúng ta quên mất việc dành cho mình vài phút tĩnh lặng để lắng nghe hơi thở và cảm nhận vẻ đẹp giản dị quanh mình. Một tách trà ấm buổi sớm, tiếng mưa rơi nhẹ bên hiên nhà hay một trang sách hay đều có thể tiếp thêm nguồn năng lượng tích cực cho cả ngày dài phía trước.",
    },
    {
        "run_no": 4,
        "scenario_group": "Voice Design - Vừa",
        "mode": "voice_design",
        "instruct": "male, middle-aged, low pitch",
        "num_step": 32,
        "speed": 1.03,
        "text": "Thị trường chứng khoán châu Á mở cửa phiên đầu tuần trong sắc xanh tích cực khi dòng vốn đầu tư công nghệ tiếp tục dẫn dắt đà phục hồi tại nhiều quốc gia.",
    },
    {
        "run_no": 5,
        "scenario_group": "Auto Voice - Ngắn/Vừa",
        "mode": "auto_voice",
        "num_step": 28,
        "speed": 1.0,
        "text": "Hệ thống tổng hợp giọng nói tự động giúp chuyển đổi văn bản thành âm thanh rõ nét và tự nhiên chỉ trong vài giây.",
    },
    # Cycle 2 (Runs 6-10)
    {
        "run_no": 6,
        "scenario_group": "Clone Voice - Ngắn",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Đừng bỏ lỡ ba mẹo tối ưu hiệu suất làm việc cực kỳ đơn giản ngay sau đây.",
    },
    {
        "run_no": 7,
        "scenario_group": "Clone Voice - Vừa (Số liệu)",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Phiên bản cập nhật 2.4 giúp rút ngắn 35% thời gian tải mô hình trên bộ nhớ đệm và tiết kiệm hơn 1.200 megabyte dung lượng bộ nhớ đồ họa.",
    },
    {
        "run_no": 8,
        "scenario_group": "Clone Voice - Dài",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Hành trình chinh phục những đỉnh cao mới luôn bắt đầu từ những bước chân nhỏ bé nhưng bền bỉ mỗi ngày. Khi đối mặt với thử thách, điều quan trọng nhất không phải là bạn đi nhanh đến đâu, mà là sự kiên định giữ vững mục tiêu, không ngừng học hỏi từ thực tế và sẵn sàng đổi mới phương pháp để tiến xa hơn.",
    },
    {
        "run_no": 9,
        "scenario_group": "Voice Design - Vừa",
        "mode": "voice_design",
        "instruct": "female, young adult, low pitch",
        "num_step": 32,
        "speed": 0.94,
        "text": "Đêm thu dịu mát trải nhẹ trên mặt hồ phẳng lặng, từng cơn gió heo may khẽ mang theo hương hoa sữa nồng nàn gợi nhớ biết bao kỷ niệm tuổi học trò.",
    },
    {
        "run_no": 10,
        "scenario_group": "Auto Voice - Ngắn/Vừa",
        "mode": "auto_voice",
        "num_step": 28,
        "speed": 1.0,
        "text": "Xin lưu ý, chuyến tàu cao tốc khởi hành lúc tám giờ mười lăm phút sáng sẽ đón khách tại sảnh số hai.",
    },
    # Cycle 3 (Runs 11-15)
    {
        "run_no": 11,
        "scenario_group": "Clone Voice - Ngắn",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Hãy nhấn đăng ký kênh ngay hôm nay để nhận thông báo về các video mới nhất.",
    },
    {
        "run_no": 12,
        "scenario_group": "Clone Voice - Vừa (Số liệu)",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Theo thống kê mới nhất ngày 24/11/2026, hơn 4.800 doanh nghiệp vừa và nhỏ đã chuyển đổi thành công sang quy trình sản xuất nội dung đa phương tiện tự động.",
    },
    {
        "run_no": 13,
        "scenario_group": "Clone Voice - Dài",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Ẩm thực Việt Nam luôn gây ấn tượng mạnh mẽ với du khách quốc tế nhờ sự kết hợp hài hòa giữa nguyên liệu tươi ngon và gia vị thanh tao. Từ bát phở nóng hổi buổi sáng miền Bắc, mâm cơm đậm đà miền Trung cho tới hương vị phóng khoáng ngọt lành của miền Tây sông nước, mỗi món ăn đều kể một câu chuyện văn hóa đầy tự hào.",
    },
    {
        "run_no": 14,
        "scenario_group": "Voice Design - Vừa",
        "mode": "voice_design",
        "instruct": "female, young adult, high pitch",
        "num_step": 32,
        "speed": 1.08,
        "text": "Siêu ưu đãi cuối năm chính thức bùng nổ với hàng ngàn phần quà hấp dẫn dành riêng cho khách hàng đặt trước trên ứng dụng ngay trong hôm nay!",
    },
    {
        "run_no": 15,
        "scenario_group": "Auto Voice - Ngắn/Vừa",
        "mode": "auto_voice",
        "num_step": 28,
        "speed": 1.0,
        "text": "Dự báo thời tiết khu vực Nam Bộ ngày mai trời nắng đẹp, chiều tối có mưa rào nhẹ vài nơi, nhiệt độ trung bình hai mươi chín độ.",
    },
    # Cycle 4 (Runs 16-20)
    {
        "run_no": 16,
        "scenario_group": "Clone Voice - Ngắn",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Cảm ơn bạn đã đồng hành và theo dõi trọn vẹn tập phát sóng đặc biệt này.",
    },
    {
        "run_no": 17,
        "scenario_group": "Clone Voice - Vừa (Số liệu)",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Thử nghiệm thực tế trên 20 phiên độc lập cho thấy độ trễ suy luận trung bình duy trì ổn định ở mức ấn tượng với chất lượng âm thanh chuẩn 24.000 hertz.",
    },
    {
        "run_no": 18,
        "scenario_group": "Clone Voice - Dài",
        "mode": "clone_voice",
        "num_step": 32,
        "speed": 1.0,
        "text": "Sự phát triển vượt bậc của các mô hình ngôn ngữ và tổng hợp âm thanh thế hệ mới đang mở ra kỷ nguyên sáng tạo không giới hạn cho các nhà làm phim độc lập, giảng viên trực tuyến và nhà xuất bản sách nói. Giờ đây, mọi ý tưởng kịch bản đều có thể hóa thành giọng kể giàu cảm xúc chỉ sau vài thao tác đơn giản.",
    },
    {
        "run_no": 19,
        "scenario_group": "Voice Design - Vừa",
        "mode": "voice_design",
        "instruct": "male, elderly, low pitch",
        "num_step": 36,
        "speed": 0.90,
        "text": "Ngày xửa ngày xưa, nơi thung lũng sương mù bên sườn núi đá vôi cao vút, có một ngôi làng nhỏ lưu giữ bí quyết chế tác đàn gỗ truyền thống qua nhiều thế hệ.",
    },
    {
        "run_no": 20,
        "scenario_group": "Auto Voice - Ngắn/Vừa",
        "mode": "auto_voice",
        "num_step": 28,
        "speed": 1.0,
        "text": "Mọi dữ liệu đo kiểm đều được ghi nhận minh bạch theo thời gian thực để phục vụ đánh giá và tối ưu hệ thống.",
    },
]


def get_wav_info(file_path: str) -> tuple[float, int, int]:
    try:
        with wave.open(file_path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            dur = round(frames / float(rate), 3) if rate > 0 else 0.0
            return dur, rate, os.path.getsize(file_path)
    except Exception:
        return 0.0, 0, os.path.getsize(file_path) if os.path.exists(file_path) else 0


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def compute_stats(vals: list[float]) -> dict:
    if not vals:
        return {"count": 0, "mean": 0.0, "median_p50": 0.0, "p90": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0, "stdev": 0.0}
    s = sorted(vals)
    return {
        "count": len(s),
        "mean": round(statistics.mean(s), 2),
        "median_p50": round(statistics.median(s), 2),
        "p90": round(percentile(s, 90), 2),
        "p95": round(percentile(s, 95), 2),
        "min": round(s[0], 2),
        "max": round(s[-1], 2),
        "stdev": round(statistics.stdev(s), 2) if len(s) > 1 else 0.0,
    }


def reset_workers(base_url: str, headers: dict, last_worker_id: str | None = None) -> bool:
    params = {}
    if last_worker_id:
        params["last_worker_id"] = last_worker_id
    for attempt in range(3):
        try:
            r = requests.post(
                f"{base_url}/v1/settings/reset-workers",
                params=params,
                headers=headers,
                timeout=60,
            )
            if r.status_code == 200:
                return True
            print(f"  [reset-workers] HTTP {r.status_code}: {r.text[:160]}", flush=True)
        except Exception as e:
            print(f"  [reset-workers] attempt {attempt+1} warning: {e}", flush=True)
        time.sleep(2.0)
    return False


def list_curated_voice_samples(base_url: str, headers: dict, explicit_id: str | None) -> list[dict]:
    if explicit_id:
        return [{"id": explicit_id, "name": explicit_id}]
    r = requests.get(f"{base_url}/v1/voice-samples", headers=headers, timeout=20)
    r.raise_for_status()
    samples = r.json()
    if not samples:
        return []
    with_ref = [s for s in samples if (s.get("ref_text") or "").strip()]
    return with_ref if with_ref else samples


def main():
    parser = argparse.ArgumentParser(description="20 Independent Cold-Start + TTS Benchmark")
    parser.add_argument("--url", default="https://voice.oloka.net", help="Base Gateway URL")
    parser.add_argument("--token", required=True, help="API Key or JWT Bearer Token")
    parser.add_argument("--voice-sample-id", default=None, help="Optional voice_sample_id")
    parser.add_argument("--runs", type=int, default=20, help="Number of independent cold-start runs (default 20)")
    parser.add_argument("--start-run", type=int, default=1, help="1-based run index to start/resume from")
    parser.add_argument("--out-dir", default="./benchmark_results/cold20_20261124", help="Output folder")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {args.token}",
        "Content-Type": "application/json",
    }

    os.makedirs(args.out_dir, exist_ok=True)
    wav_dir = os.path.join(args.out_dir, "wavs")
    os.makedirs(wav_dir, exist_ok=True)
    jsonl_path = os.path.join(args.out_dir, "runs.jsonl")
    summary_path = os.path.join(args.out_dir, "summary.json")

    available_samples = list_curated_voice_samples(base_url, headers, args.voice_sample_id)
    sample_id = available_samples[0]["id"] if available_samples else None
    sample_name = available_samples[0].get("name") if available_samples else None
    print(f"=== OMNIVOICE 20-RUN INDEPENDENT COLD-START BENCHMARK ===", flush=True)
    print(f"Gateway:       {base_url}", flush=True)
    print(f"Clone Samples: {len(available_samples)} available (primary: {sample_id} - {sample_name})", flush=True)
    print(f"Output Dir:    {os.path.abspath(args.out_dir)}", flush=True)

    # Load existing runs if resuming
    completed_records = []
    if args.start_run > 1 and os.path.exists(jsonl_path):
        with open(jsonl_path, "r", encoding="utf-8") as jf:
            for line in jf:
                if line.strip():
                    completed_records.append(json.loads(line))

    last_worker = completed_records[-1].get("worker_id") if completed_records else None

    print(f"\n[Pre-flight] Resetting any active Kaggle GPU workers before starting...", flush=True)
    reset_workers(base_url, headers, last_worker_id=last_worker)
    time.sleep(3.0)

    selected_scenarios = SCENARIOS[: args.runs]
    for sc in selected_scenarios:
        run_no = sc["run_no"]
        if run_no < args.start_run:
            continue

        mode = sc["mode"]
        payload = {
            "mode": mode,
            "text": sc["text"],
            "speed": sc["speed"],
            "num_step": sc["num_step"],
            "denoise": True,
            "normalize_text": True,
        }
        used_sample_id = None
        used_sample_name = None
        if mode == "clone_voice":
            if available_samples:
                cycle_idx = (run_no - 1) // 5
                chosen_s = available_samples[cycle_idx % len(available_samples)]
                used_sample_id = chosen_s["id"]
                used_sample_name = chosen_s.get("name") or used_sample_id
                payload["voice_sample_id"] = used_sample_id
            else:
                payload["mode"] = "auto_voice"
        elif mode == "voice_design":
            payload["instruct"] = sc.get("instruct", "female, young adult, low pitch")

        word_count = len(sc["text"].split())
        char_count = len(sc["text"])
        print(
            f"\n[Run {run_no:02d}/{args.runs:02d}] {sc['scenario_group']} | mode={payload['mode']} | {word_count} words ({char_count} chars) | steps={sc['num_step']}",
            flush=True,
        )

        t_submit0 = time.perf_counter()
        started_iso = datetime.utcnow().isoformat() + "Z"
        try:
            create_res = requests.post(f"{base_url}/v1/tts/jobs", json=payload, headers=headers, timeout=30)
            create_res.raise_for_status()
            job_info = create_res.json()
            job_id = job_info["job_id"]
        except Exception as ce:
            print(f"  [!] Create job failed on Run {run_no}: {ce}", flush=True)
            rec = {
                "run_no": run_no,
                "scenario_group": sc["scenario_group"],
                "mode": payload["mode"],
                "status": "failed",
                "error": str(ce),
            }
            completed_records.append(rec)
            with open(jsonl_path, "a", encoding="utf-8") as jf:
                jf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            reset_workers(base_url, headers, last_worker_id=last_worker)
            time.sleep(4.0)
            continue

        api_create_ms = round((time.perf_counter() - t_submit0) * 1000.0, 1)
        print(f"  -> Created {job_id} ({api_create_ms} ms). Waiting for Cold-Start GPU boot + TTS...", flush=True)

        last_st = None
        final_data = None
        first_queued_kaggle_s = None
        first_running_kaggle_s = None
        first_worker_claimed_s = None
        poll_deadline = time.perf_counter() + 480.0

        while time.perf_counter() < poll_deadline:
            elapsed_s = round(time.perf_counter() - t_submit0, 2)
            try:
                pr = requests.get(f"{base_url}/v1/jobs/{job_id}?t={int(time.time()*1000)}", headers=headers, timeout=20)
                if pr.status_code == 200:
                    pdata = pr.json()
                    st = pdata.get("status")
                    prog = pdata.get("progress", 0)
                    msg = pdata.get("message") or ""

                    if st == "queued_kaggle" and first_queued_kaggle_s is None:
                        first_queued_kaggle_s = elapsed_s
                    if st == "starting_worker" and prog >= 25 and first_running_kaggle_s is None:
                        first_running_kaggle_s = elapsed_s
                    if pdata.get("started_at") and first_worker_claimed_s is None:
                        first_worker_claimed_s = elapsed_s

                    if st != last_st:
                        print(f"     [+{elapsed_s:5.1f}s] {st} ({prog}%) — {msg}", flush=True)
                        last_st = st

                    if st in ("completed", "failed", "cancelled"):
                        final_data = pdata
                        break
            except Exception as pe:
                print(f"     [+{elapsed_s:5.1f}s] transient poll warning: {pe}", flush=True)
            time.sleep(1.2)

        t_e2e = round(time.perf_counter() - t_submit0, 2)

        if not final_data or final_data.get("status") != "completed":
            err_detail = (final_data or {}).get("error_message") or "Timeout waiting for completion"
            print(f"  [!] Run {run_no} FAILED after {t_e2e}s: {err_detail}", flush=True)
            used_w = (final_data or {}).get("worker_id") or last_worker
            rec = {
                "run_no": run_no,
                "job_id": job_id,
                "scenario_group": sc["scenario_group"],
                "mode": payload["mode"],
                "status": "failed",
                "total_e2e_s": t_e2e,
                "error": err_detail,
            }
            completed_records.append(rec)
            with open(jsonl_path, "a", encoding="utf-8") as jf:
                jf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            reset_workers(base_url, headers, last_worker_id=used_w)
            time.sleep(5.0)
            continue

        # Download generated WAV and measure duration
        wav_path = os.path.join(wav_dir, f"run_{run_no:02d}_{job_id}.wav")
        audio_url_rel = final_data.get("audio_url") or f"/v1/tts/jobs/{job_id}/audio"
        dl_url = final_data.get("cdn_audio_url") or f"{base_url}{audio_url_rel}"
        wav_dur, wav_sr, wav_bytes = 0.0, 0, 0
        try:
            ar = requests.get(dl_url, headers={"Authorization": f"Bearer {args.token}"}, timeout=45)
            if ar.status_code == 200:
                with open(wav_path, "wb") as wf:
                    wf.write(ar.content)
                wav_dur, wav_sr, wav_bytes = get_wav_info(wav_path)
        except Exception as de:
            print(f"  [!] Warning downloading WAV for {job_id}: {de}", flush=True)

        # Extract server-authoritative worker boot time & audio gen time
        srv_boot_s = round(float(final_data.get("queue_time") or 0.0), 2)
        srv_gen_s = round(float(final_data.get("processing_time") or 0.0), 2)
        srv_total_s = round(float(final_data.get("total_time") or (srv_boot_s + srv_gen_s)), 2)
        used_worker = final_data.get("worker_id") or "worker_1"
        last_worker = used_worker
        rtf = round(srv_gen_s / wav_dur, 3) if wav_dur > 0 else 0.0

        print(
            f"  [✓] Run {run_no:02d} DONE on {used_worker}: "
            f"Worker Boot = {srv_boot_s:.2f}s | Gen Audio = {srv_gen_s:.2f}s | "
            f"Total = {srv_total_s:.2f}s | WAV = {wav_dur:.2f}s ({rtf:.2f}x RTF)",
            flush=True,
        )

        rec = {
            "run_no": run_no,
            "job_id": job_id,
            "started_utc": started_iso,
            "scenario_group": sc["scenario_group"],
            "mode": payload["mode"],
            "voice_sample_id": used_sample_id,
            "voice_sample_name": used_sample_name,
            "instruct": payload.get("instruct"),
            "num_step": sc["num_step"],
            "speed": sc["speed"],
            "input_words": word_count,
            "input_chars": char_count,
            "normalized_text": final_data.get("text") or sc["text"],
            "worker_id": used_worker,
            "status": "completed",
            "api_create_ms": api_create_ms,
            "worker_boot_s": srv_boot_s,
            "audio_gen_s": srv_gen_s,
            "server_total_s": srv_total_s,
            "client_e2e_s": t_e2e,
            "wav_duration_s": wav_dur,
            "wav_sample_rate": wav_sr,
            "wav_size_bytes": wav_bytes,
            "rtf": rtf,
        }
        completed_records.append(rec)
        with open(jsonl_path, "a", encoding="utf-8") as jf:
            jf.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # Tear down Kaggle worker & reset WorkerSession so NEXT run is 100% independent Cold-Start!
        if run_no < args.runs:
            print(f"  [Teardown] Terminating {used_worker} on Kaggle for clean Cold-Start on Run {run_no+1:02d}...", flush=True)
            reset_workers(base_url, headers, last_worker_id=used_worker)
            time.sleep(4.0)

    # Compute comprehensive aggregate statistics
    ok_runs = [r for r in completed_records if r.get("status") == "completed"]
    boot_vals = [r["worker_boot_s"] for r in ok_runs]
    gen_vals = [r["audio_gen_s"] for r in ok_runs]
    tot_vals = [r["server_total_s"] for r in ok_runs]
    rtf_vals = [r["rtf"] for r in ok_runs if r.get("rtf", 0) > 0]
    dur_vals = [r["wav_duration_s"] for r in ok_runs if r.get("wav_duration_s", 0) > 0]

    by_scenario = {}
    for grp in sorted({r["scenario_group"] for r in ok_runs}):
        sub = [r for r in ok_runs if r["scenario_group"] == grp]
        by_scenario[grp] = {
            "runs": len(sub),
            "worker_boot_s": compute_stats([x["worker_boot_s"] for x in sub]),
            "audio_gen_s": compute_stats([x["audio_gen_s"] for x in sub]),
            "server_total_s": compute_stats([x["server_total_s"] for x in sub]),
            "wav_duration_s": compute_stats([x["wav_duration_s"] for x in sub if x.get("wav_duration_s", 0) > 0]),
            "rtf": compute_stats([x["rtf"] for x in sub if x.get("rtf", 0) > 0]),
        }

    summary = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "gateway_url": base_url,
        "voice_sample_id": sample_id,
        "voice_sample_name": sample_name,
        "total_requested_runs": args.runs,
        "successful_runs": len(ok_runs),
        "failed_runs": len(completed_records) - len(ok_runs),
        "overall": {
            "worker_boot_s": compute_stats(boot_vals),
            "audio_gen_s": compute_stats(gen_vals),
            "server_total_s": compute_stats(tot_vals),
            "wav_duration_s": compute_stats(dur_vals),
            "rtf": compute_stats(rtf_vals),
        },
        "by_scenario": by_scenario,
        "runs": completed_records,
    }

    with open(summary_path, "w", encoding="utf-8") as sf_out:
        json.dump(summary, sf_out, ensure_ascii=False, indent=2)

    print("\n" + "=" * 88, flush=True)
    print(f" HOÀN TẤT BENCHMARK {len(ok_runs)}/{args.runs} LẦN COLD-START ĐỘC LẬP", flush=True)
    print("=" * 88, flush=True)
    print(f" Worker Cold-Start (s): {summary['overall']['worker_boot_s']}", flush=True)
    print(f" Audio Generation  (s): {summary['overall']['audio_gen_s']}", flush=True)
    print(f" Total End-to-End  (s): {summary['overall']['server_total_s']}", flush=True)
    print(f" Real-Time Factor (RTF): {summary['overall']['rtf']}", flush=True)
    print(f" Saved summary -> {summary_path}", flush=True)


if __name__ == "__main__":
    main()
