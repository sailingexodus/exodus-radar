#!/usr/bin/env python3
# Exodus Radar - Copyright 2026 Sailing Exodus (https://sailingexodus.net)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Free to use on your own boat and for any non-commercial purpose. Want to use it in something you sell? Just ask: https://sailingexodus.net
"""Anchor watch - a plain circle around where the anchor went down.

Set from the box on the radar page (radar-display.py). It only does this:
  * toggle on  -> remembers the boat's GPS position as the anchor point
  * every 5 s  -> distance from that point, in feet
  * past the set distance (2 readings in a row) -> says it out loud on the boat's
    speaker and sends a Telegram message, then repeats until it is switched off
    or the boat is back inside
  * GPS missing or stale for 90 s -> the same alarm, because a watch that cannot
    see is not a watch

Runs as its own service so it keeps watching when the radar page is closed.
Config the page writes:   /home/pi/.anchor-watch.json
Status the page reads:    /home/pi/.anchor-watch-status.json
Telegram settings are read from the same file boat-alerts.py uses.
"""
import json
import math
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

CFG = os.environ.get("ANCHOR_CFG", "/home/pi/.anchor-watch.json")
STATUS = os.environ.get("ANCHOR_STATUS", "/home/pi/.anchor-watch-status.json")
TELEGRAM = "/home/pi/.boat-telegram"
SK = "http://127.0.0.1:3000/signalk/v1/api/vessels/self/navigation/position"
SOUND = "/home/pi/boat-bot/sound-alarm.sh"
TEST = os.environ.get("ANCHOR_TEST") == "1"          # label messages TEST, no sound

POLL = 5
OUTSIDE_NEED = 2            # readings in a row beyond the circle before alarming
BACK_INSIDE = 0.95          # must come back inside 95 % of the radius to clear
NOGPS_AFTER = 90            # seconds without a fresh fix
SOUND_EVERY = 60            # seconds between spoken alarms while it persists
TG_EVERY = 300              # seconds between Telegram reminders while it persists
FT = 3.28084


def load_cfg():
    try:
        with open(CFG) as f:
            d = json.load(f)
    except Exception:
        d = {}
    return {"armed": bool(d.get("armed", False)),
            "radius_ft": float(d.get("radius_ft", 150)),
            "anchor": d.get("anchor")}


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def telegram_conf():
    c = {}
    try:
        with open(TELEGRAM) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    c[k.strip()] = v.strip()
    except Exception:
        pass
    return c.get("BOT_TOKEN"), c.get("ALLOWED_CHAT")


def tg(text):
    if TEST:
        text = "TEST - " + text
    token, chat = telegram_conf()
    if not token or not chat:
        print("telegram not configured", flush=True)
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % token,
                               data=data, timeout=25).read()
        return True
    except Exception as e:
        print("telegram failed: %s" % e, flush=True)
        return False


def speak(words):
    if TEST:
        print("(test) would say: " + words, flush=True)
        return
    try:
        subprocess.Popen([SOUND, words, "3", "1"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception as e:
        print("sound failed: %s" % e, flush=True)


def position():
    """(lat, lon, age_seconds) or None. A missing, unparseable or future timestamp counts as STALE (a huge
    age), never as fresh, so a watch is never armed or kept alive on a position of unknown age."""
    try:
        d = json.load(urllib.request.urlopen(SK, timeout=4))
        v = d["value"]
        lat, lon = float(v["latitude"]), float(v["longitude"])
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return None
        age = 1e9
        ts = d.get("timestamp", "")
        if ts:
            from datetime import datetime, timezone
            t = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            age = time.time() - t.timestamp()
            if age < -30:
                age = 1e9
        return lat, lon, age
    except Exception:
        return None


def dist_ft(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a)) * FT


def main():
    outside, alarm_since, last_sound, last_tg = 0, None, 0.0, 0.0
    nogps_since, nogps_alarm = None, False
    was_armed = False
    print("anchor-watch started", flush=True)
    while True:
        cfg = load_cfg()
        pos = position()
        fresh = pos is not None and pos[2] <= NOGPS_AFTER
        st = {"t": time.time(), "armed": cfg["armed"], "radius_ft": cfg["radius_ft"],
              "state": "off", "dist_ft": None, "anchor": cfg["anchor"]}

        if not cfg["armed"]:
            if was_armed and alarm_since is not None:
                tg("Anchor watch switched OFF.")
            outside, alarm_since, nogps_since, nogps_alarm = 0, None, None, False
            was_armed = False
            save_json(STATUS, st)
            time.sleep(POLL)
            continue

        was_armed = True
        if cfg["anchor"] is None:
            if fresh:
                cfg["anchor"] = {"lat": pos[0], "lon": pos[1], "set": time.strftime("%Y-%m-%d %H:%M:%S")}
                save_json(CFG, {"armed": True, "radius_ft": cfg["radius_ft"], "anchor": cfg["anchor"]})
                st["anchor"] = cfg["anchor"]
                tg("Anchor watch ON. Circle %d ft around %.5f, %.5f." %
                   (cfg["radius_ft"], cfg["anchor"]["lat"], cfg["anchor"]["lon"]))
            else:
                st["state"] = "nogps"
                save_json(STATUS, st)
                time.sleep(POLL)
                continue

        if not fresh:
            st["state"] = "nogps"
            nogps_since = nogps_since or time.time()
            if time.time() - nogps_since >= NOGPS_AFTER:
                if not nogps_alarm:
                    nogps_alarm = True
                    tg("ANCHOR WATCH CANNOT SEE GPS. No fresh position for %d seconds. "
                       "Nothing is watching the anchor." % NOGPS_AFTER)
                    last_tg = time.time()
                if time.time() - last_sound >= SOUND_EVERY * 2:
                    speak("Anchor watch has lost the GPS")
                    last_sound = time.time()
                st["state"] = "nogps_alarm"
        else:
            if nogps_alarm:
                tg("Anchor watch has GPS again.")
            nogps_since, nogps_alarm = None, False
            a = cfg["anchor"]
            d = dist_ft(a["lat"], a["lon"], pos[0], pos[1])
            st["dist_ft"] = round(d, 1)
            if d > cfg["radius_ft"]:
                outside += 1
            elif d <= cfg["radius_ft"] * BACK_INSIDE:
                outside = 0
            if outside >= OUTSIDE_NEED:
                st["state"] = "alarm"
                now = time.time()
                if now - last_sound >= SOUND_EVERY or last_sound == 0:
                    speak("Anchor alarm. The boat has moved %d feet" % d)
                    last_sound = now
                if alarm_since is None:
                    alarm_since = now
                    tg("ANCHOR ALARM. The boat is %d ft from the anchor point, circle is %d ft. "
                       "Position %.5f, %.5f." % (d, cfg["radius_ft"], pos[0], pos[1]))
                    last_tg = now
                elif now - last_tg >= TG_EVERY:
                    tg("ANCHOR ALARM still active. %d ft from the anchor point (circle %d ft)." %
                       (d, cfg["radius_ft"]))
                    last_tg = now
            else:
                st["state"] = "ok"
                if alarm_since is not None and outside == 0:
                    tg("Anchor alarm cleared. Boat is back inside the circle (%d ft out of %d)." %
                       (d, cfg["radius_ft"]))
                    alarm_since = None
        save_json(STATUS, st)
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
