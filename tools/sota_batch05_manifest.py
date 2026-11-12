"""One-shot: specs.json + MANIFEST.md for sota batch05 (EP399/401/403/404/405/406/462)."""
import glob
import json
import os

b = "C:/Azazael/state/sota_batches/batch05"
vids = {
    "Wv1SCfVl-XY": (462, "2608.25661v1",
                    "EP462: OpsHarness turns general AI into SRE experts",
                    "Wed, 30 Sep 2026 22:00:00 GMT"),
    "WOmP8WOUcyA": (399, "2607.23771v1",
                    "EP399: Training AI to follow any reasoning workflow",
                    "Sun, 30 Aug 2026 14:00:00 GMT"),
    "d8Das3pccSE": (401, "2607.24280v1",
                    "EP401: Extracting Pure Reasoning From AI Giants",
                    "Mon, 31 Aug 2026 14:00:00 GMT"),
    "l6oLNXZ9u1g": (403, "2607.25400v1",
                    "EP403: COVENANT keeps AI agents on the rails",
                    "Tue, 01 Sep 2026 14:00:00 GMT"),
    "iJNXmvBYyHU": (404, "2607.26120v1",
                    "EP404: AI agents hide betrayal in Werewolf",
                    "Tue, 01 Sep 2026 22:00:00 GMT"),
    "OqroezZ3wjc": (405, "2607.26773v1",
                    "EP405: Are AI Agents Just Talking to Themselves",
                    "Wed, 02 Sep 2026 14:00:00 GMT"),
    "GPTvf2xwmrE": (406, "2607.26598v1",
                    "EP406: Ending AI Groundhog Day With Living Harness",
                    "Wed, 02 Sep 2026 22:00:00 GMT"),
}
specs = {}
mlines = ["# SOTA batch05 — next-harvest 7 (EP399/401/403/404/405/406/462)", ""]
for vid, (ep, ax, title, pub) in vids.items():
    with open(os.path.join(b, vid + ".info.json"),
              encoding="utf-8", errors="replace") as fh:
        info = json.load(fh)
    mb = os.path.getsize(os.path.join(b, vid + ".mp4")) / 1e6
    dur = float(info.get("duration") or 0)
    specs[vid] = {"yt_id": vid, "title": title, "dur": round(dur, 2),
                  "w": info.get("width"), "h": info.get("height"),
                  "mb": round(mb, 2), "ep": ep, "arxiv": ax, "pub": pub,
                  "mid": "frames/" + vid + "_mid.jpg",
                  "strip": "frames/" + vid + "_strip.jpg"}
    mlines += ["## EP" + str(ep) + ": " + title,
               "YouTube: https://www.youtube.com/watch?v=" + vid,
               "arXiv: " + ax + " | published: " + pub,
               "Video: %s.mp4 (%.2fMB, %.0fs, %sx%s)"
               % (vid, mb, dur, info.get("width"), info.get("height")),
               "Mid: frames/%s_mid.jpg | Strip: frames/%s_strip.jpg"
               % (vid, vid), ""]
with open(os.path.join(b, "specs.json"), "w", encoding="utf-8") as fh:
    json.dump(specs, fh, indent=1)
with open(os.path.join(b, "MANIFEST.md"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(mlines))
print("specs+manifest written")
print("mp4:", sorted(os.path.basename(p) for p in glob.glob(b + "/*.mp4")))
print("tech:", sorted(os.path.basename(p)
                      for p in glob.glob(b + "/technique/*.txt")))
print("total MB:", round(sum(os.path.getsize(p)
                             for p in glob.glob(b + "/*.mp4")) / 1e6, 1))
