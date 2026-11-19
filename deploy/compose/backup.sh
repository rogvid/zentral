#!/bin/sh
# Database dumps for the `backup` service (compose.yaml, profile "backup").
#
#   loop    dump once a day (at start-up too, if the newest dump is a day old)
#   once [LABEL]   dump now (LABEL is added to the file name)
#   check   healthy while the newest dump is less than 26 hours old
#
# Writes /backups/<database>-<UTC timestamp>.dump (pg_dump custom format,
# mode 600) for each database in BACKUP_DATABASES, and deletes dumps older
# than BACKUP_KEEP_DAYS. Dumps hold encrypted content but readable metadata:
# copy them off the host, encrypted, and keep the keys elsewhere.
set -eu
umask 077
dir=/backups
keep=${BACKUP_KEEP_DAYS:-14}

once() {  # [label]
  stamp=$(date -u +%Y%m%dT%H%M%SZ)${1:+-$1}
  for db in ${BACKUP_DATABASES:-zentral}; do
    file="$dir/$db-$stamp.dump"
    n=1
    while [ -e "$file" ]; do  # never overwrite an existing dump
      n=$((n + 1))
      file="$dir/$db-$stamp-$n.dump"
    done
    # Explicit checks: errexit is off when the caller tests our status.
    pg_dump --format=custom --dbname="$db" --file="$file.part" || { rm -f "$file.part"; return 1; }
    mv "$file.part" "$file" || return 1
    echo "backup: wrote $file ($(du -h "$file" | cut -f1))"
  done
  find "$dir" -name '*.dump' -mtime +"$keep" -print -delete | sed 's/^/backup: deleted /'
  rm -f "$dir"/*.part
}

fresh() {  # a dump newer than $1 minutes exists
  [ -n "$(find "$dir" -name '*.dump' -mmin -"$1" | head -n 1)" ]
}

case "${1:-loop}" in
  once) once "${2:-}" ;;
  check) fresh 1560 ;;
  loop)
    echo "backup: daily dumps of ${BACKUP_DATABASES:-zentral} to $dir, kept $keep days"
    while true; do
      if ! fresh 1380; then once || echo "backup: FAILED" >&2; fi
      sleep 3600
    done
    ;;
  *) echo "usage: $0 loop|once|check" >&2; exit 2 ;;
esac
