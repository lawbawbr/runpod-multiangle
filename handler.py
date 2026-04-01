"""
RunPod Serverless Handler — Multiangle Photo
Input:  { "photo_url": "https://..." }
Output: { "images": { "original": "https://s3...", "front": "...", "left": "...", "right": "..." } }
"""

import os, copy, json, time, uuid, tempfile
import httpx, boto3
from botocore.client import Config
import runpod

COMFY_BASE  = "http://localhost:8188"
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "https://s3.strategy-ia.art")
S3_ACCESS   = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET   = os.environ.get("S3_SECRET_KEY", "")
S3_BUCKET   = os.environ.get("S3_BUCKET", "ffmpeg")

ANGLES = [
    {"h": 0,   "v": 0, "z": 1.5, "label": "front"},
    {"h": 90,  "v": 0, "z": 1.5, "label": "left"},
    {"h": 270, "v": 0, "z": 1.5, "label": "right"},
]

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "multiangle.api")


# ── S3 ────────────────────────────────────────────────────────────────────────

def s3_upload(local_path: str, key: str) -> str:
    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS,
        aws_secret_access_key=S3_SECRET,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    s3.upload_file(local_path, S3_BUCKET, key)
    return f"{S3_ENDPOINT}/{S3_BUCKET}/{key}"


# ── ComfyUI helpers ───────────────────────────────────────────────────────────

def wait_for_comfyui(timeout=180):
    print("[comfyui] waiting...")
    for _ in range(timeout // 3):
        try:
            r = httpx.get(f"{COMFY_BASE}/system_stats", timeout=5)
            if r.status_code == 200:
                print("[comfyui] ready")
                return
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError("ComfyUI did not start")


def upload_to_comfyui(local_path: str, name: str) -> str:
    with open(local_path, "rb") as f:
        r = httpx.post(
            f"{COMFY_BASE}/upload/image",
            files={"image": (name, f, "application/octet-stream")},
            timeout=120,
        )
    r.raise_for_status()
    return r.json()["name"]


def submit_prompt(prompt: dict) -> str:
    r = httpx.post(f"{COMFY_BASE}/prompt", json={"prompt": prompt}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("node_errors"):
        raise RuntimeError(f"Node errors: {data['node_errors']}")
    return data["prompt_id"]


def wait_for_job(pid: str, timeout=600) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = httpx.get(f"{COMFY_BASE}/history/{pid}", timeout=10)
            if r.status_code == 200 and r.content:
                data = r.json()
                if pid in data:
                    entry = data[pid]
                    status = entry.get("status", {})
                    if status.get("completed"):
                        return entry.get("outputs", {})
                    if status.get("status_str") == "error":
                        raise RuntimeError(f"Job failed: {status}")
        except RuntimeError:
            raise
        except Exception:
            pass
        elapsed = int(time.time() - start)
        if elapsed % 30 == 0 and elapsed > 0:
            print(f"  [{pid[:8]}] {elapsed}s...")
        time.sleep(5)
    raise TimeoutError(f"Job {pid} timed out")


# ── Handler ───────────────────────────────────────────────────────────────────

def handler(job):
    inp = job["input"]
    photo_url = inp.get("photo_url")
    if not photo_url:
        return {"error": "photo_url is required"}

    print(f"[handler] photo_url={photo_url[-60:]}")
    wait_for_comfyui()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Download photo
        print("[handler] downloading photo...")
        photo_path = os.path.join(tmpdir, "photo.jpg")
        r = httpx.get(photo_url, timeout=120, follow_redirects=True)
        r.raise_for_status()
        with open(photo_path, "wb") as f:
            f.write(r.content)
        print(f"  {len(r.content)//1024}KB")

        # Upload original to ComfyUI
        photo_name = upload_to_comfyui(photo_path, "sl_photo.jpg")
        print(f"  ComfyUI name: {photo_name}")

        # Upload original to S3
        run_id = uuid.uuid4().hex
        orig_key = f"multiangle/{run_id}/original.jpg"
        orig_url = s3_upload(photo_path, orig_key)
        print(f"  original -> {orig_url}")

        result_images = {"original": orig_url}

        # Load template
        with open(TEMPLATE_PATH) as f:
            base_prompt = json.load(f)

        # Submit 3 angle jobs
        prompt_ids = []
        for a in ANGLES:
            prompt = copy.deepcopy(base_prompt)
            prompt["1"]["inputs"]["image"] = photo_name
            prompt["3"]["inputs"]["horizontal_angle"] = a["h"]
            prompt["3"]["inputs"]["vertical_angle"] = a["v"]
            prompt["3"]["inputs"]["zoom"] = a["z"]
            prompt["2"]["inputs"]["filename_prefix"] = f"sl_{a['label']}"
            pid = submit_prompt(prompt)
            prompt_ids.append((a["label"], pid))
            print(f"  {a['label']} -> {pid}")

        # Wait and upload results
        for label, pid in prompt_ids:
            print(f"  waiting {label}...")
            outputs = wait_for_job(pid)
            for node_out in outputs.values():
                for item in node_out.get("images", []):
                    fname = item.get("filename", "")
                    if not fname:
                        continue
                    r2 = httpx.get(
                        f"{COMFY_BASE}/view",
                        params={"filename": fname, "subfolder": item.get("subfolder", ""), "type": "output"},
                        timeout=120,
                    )
                    img_path = os.path.join(tmpdir, f"{label}.png")
                    with open(img_path, "wb") as f:
                        f.write(r2.content)
                    key = f"multiangle/{run_id}/{label}.png"
                    url = s3_upload(img_path, key)
                    result_images[label] = url
                    print(f"  {label} -> {url} ({len(r2.content)//1024}KB)")
                    break

    print(f"[handler] done: {list(result_images.keys())}")
    return {"images": result_images}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
