#!/bin/sh
#
# kiosk-status.sh - last sign-of-life per kioskage host, parsed from your
# content server's nginx access log.
#
# kioskage sticks tag their kiosk requests with &host=<hostname>&v=<version>
# (added by the stick at kiosk launch), and kioskage.js forwards those params
# on every version poll (~every 3 min). Anonymous visitors of the plain shared
# URL carry no host tag, so they are not counted here.
#
# Run on the site host (reads the nginx access log; usually needs sudo):
#   sudo sh kiosk-status.sh [/var/log/nginx/access.log]
#
LOG=${1:-/var/log/nginx/access.log}
[ -r "$LOG" ] || { echo "cannot read $LOG (try: sudo sh $0 $LOG)" >&2; exit 1; }
NOW=$(date -u +%s)
TAB=$(printf '\t')

{
  printf 'HOST\tIP\tVERSION\tLAST_SEEN_UTC\tAGE\n'
  grep -a 'host=' "$LOG" | awk -v NOW="$NOW" '
    function qp(uri, name,   n, a, i) {
      n = split(uri, a, /[?&]/)
      for (i = 1; i <= n; i++)
        if (index(a[i], name "=") == 1) return substr(a[i], length(name) + 2)
      return ""
    }
    function mnum(m) { return (index("JanFebMarAprMayJunJulAugSepOctNovDec", m) + 2) / 3 }
    # UTC epoch without mktime (FreeBSD base awk has no mktime/systime).
    function toepoch(y, mo, d, h, mi, s,   days, i, md, n) {
      days = 0
      for (i = 1970; i < y; i++)
        days += (i % 4 == 0 && (i % 100 != 0 || i % 400 == 0)) ? 366 : 365
      n = split("31 28 31 30 31 30 31 31 30 31 30 31", md, " ")
      if (mo > 2 && (y % 4 == 0 && (y % 100 != 0 || y % 400 == 0))) days++
      for (i = 1; i < mo; i++) days += md[i]
      days += d - 1
      return days * 86400 + h * 3600 + mi * 60 + s
    }
    /kiosk/ {
      ip = $1
      ts = ""
      if (match($0, /\[[^]]+\]/)) ts = substr($0, RSTART + 1, RLENGTH - 2)
      if (match($0, /"[A-Z]+ [^ ]+ HTTP/)) {
        req = substr($0, RSTART + 1, RLENGTH - 1)
        split(req, r, " ")
        h = qp(r[2], "host")
        if (h != "") { LIP[h] = ip; LV[h] = qp(r[2], "v"); LT[h] = ts }
      }
    }
    END {
      for (h in LT) {
        split(LT[h], p, /[\/: ]/)         # day mon year hh mm ss (+tz)
        e = toepoch(p[3], mnum(p[2]), p[1], p[4], p[5], p[6])
        age = NOW - e; if (age < 0) age = 0
        if (age < 3600)       a = int(age / 60) "m"
        else if (age < 86400) a = int(age / 3600) "h"
        else                  a = int(age / 86400) "d"
        printf "%s\t%s\t%s\t%s\t%s\t%d\n", h, LIP[h], (LV[h] == "" ? "-" : LV[h]), LT[h], a, e
      }
    }
  ' | sort -t"$TAB" -k6,6 -rn | cut -f1-5
} | column -t -s "$TAB"
