#!/usr/bin/env bash
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
#
# The multi-page speed run on one spot VM: PDFluent 1.1.0 (PyPI image) and a
# release-candidate wheel (WHEEL=<file>, rc image) over the 40 arXiv papers in
# datasets/multipage-arxiv-40.json, interleaved, 3 repetitions, 1 CPU and all
# CPUs. THIS SPENDS MONEY. Without CONFIRM=yes it prints the plan and creates
# nothing. The VM is deleted when the script exits (trap) and by GCP at the
# lifetime cap (HOURS, default 3).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || exit 1
PROJECT="${PROJECT:-pdfluent}"; ZONE="${ZONE:-europe-west4-a}"; MACHINE="${MACHINE:-c3-standard-8}"
HOURS="${HOURS:-3}"; CONFIRM="${CONFIRM:-no}"; REPS="${REPS:-3}"
: "${WHEEL:?set WHEEL to the release-candidate wheel}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"; LOGS="$ROOT/work/gcp/mp-$stamp"; mkdir -p "$LOGS"
name="pdfluent-bench-mp-$(echo "$stamp" | tr 'TZ' 'tz')"
create=(gcloud compute instances create "$name" --project="$PROJECT" --zone="$ZONE" --machine-type="$MACHINE"
  --provisioning-model=SPOT --instance-termination-action=DELETE --max-run-duration="${HOURS}h"
  --boot-disk-size=100GB --boot-disk-type=pd-balanced --labels="purpose=pdfluent-bench,run=mp"
  --scopes=default --image-project=debian-cloud --image-family=debian-12)
ssh=(gcloud compute ssh "$name" --project="$PROJECT" --zone="$ZONE" --quiet)
scp=(gcloud compute scp --project="$PROJECT" --zone="$ZONE" --quiet)
cat > "$LOGS/remote.sh" <<REMOTE
set -euo pipefail
trap 'echo "\$?" > ~/round.done' EXIT
sudo bash /tmp/vm_setup.sh cpu
git clone -q ~/bench.bundle ~/pdfluent-bench
cd ~/pdfluent-bench
cp ~/rc-wheel/*.whl docker/pdfluent-rc/wheels/
sudo docker/build.sh pdfluent pdfluent-rc
python3 scripts/multipage_fetch.py work/multipage/pdfs
mkdir -p mpout
{ echo "== lscpu"; lscpu; echo "== nproc \$(nproc)"; echo "== docker"; docker --version; } > mpout/machine.txt
cp work/images.json mpout/images.json
rev="\$(git rev-parse --short HEAD)"; echo "\$rev" > mpout/harness-commit.txt
for rep in \$(seq 1 $REPS); do
  for thr in 1 all; do
    if [ \$((rep % 2)) = 1 ]; then order="pdfluent pdfluent-rc"; else order="pdfluent-rc pdfluent"; fi
    for fam in \$order; do
      tag="pdfluent-bench/\$fam:\$rev"
      cpus=""; [ "\$thr" = 1 ] && cpus="--cpuset-cpus=0"
      sudo docker run --rm --memory=12g \$cpus -v "\$PWD:/bench:ro" -v "\$PWD/work:/work:ro" -v "\$PWD/mpout:/out" "\$tag" \\
        /opt/ex/pdfluent/bin/python /bench/scripts/multipage_speed.py /bench/datasets/multipage-arxiv-40.json /work/multipage/pdfs "/out/\${fam}_t\${thr}_r\${rep}.json"
    done
  done
done
sudo chown -R "\$USER" mpout
tar czf ~/results.tgz mpout
REMOTE
if [ "$CONFIRM" != yes ]; then
  printf '%q ' "${create[@]}"; echo; cat "$LOGS/remote.sh"; echo "=== plan only. CONFIRM=yes runs it."; exit 0
fi
trap 'gcloud compute instances delete "'"$name"'" --project="'"$PROJECT"'" --zone="'"$ZONE"'" --quiet || echo "TEARDOWN FAILED for '"$name"'" >&2' EXIT
"${create[@]}" > "$LOGS/create.log" 2>&1
for _ in $(seq 1 30); do "${ssh[@]}" --command=true > /dev/null 2>&1 && break; sleep 10; done
git bundle create "$LOGS/bench.bundle" HEAD > /dev/null
"${scp[@]}" "$LOGS/bench.bundle" "$name:~/bench.bundle"
"${scp[@]}" infra/gcp/vm_setup.sh "$name:/tmp/vm_setup.sh"
"${ssh[@]}" --command="mkdir -p ~/rc-wheel"; "${scp[@]}" "$WHEEL" "$name:~/rc-wheel/"
"${scp[@]}" "$LOGS/remote.sh" "$name:~/round.sh"
"${ssh[@]}" --command="nohup bash ~/round.sh > ~/round.log 2>&1 < /dev/null & echo started"
while :; do
  sleep 60
  state="$("${ssh[@]}" --command="cat ~/round.done 2>/dev/null || echo running" 2>/dev/null | tail -n 1)" || state=""
  if [ -z "$state" ]; then
    gcloud compute instances describe "$name" --project="$PROJECT" --zone="$ZONE" --format='value(status)' > /dev/null 2>&1 || { echo "LOST: $name is gone" >&2; exit 1; }
    continue
  fi
  echo "$(date -u +%FT%TZ) $state" >> "$LOGS/poll.log"
  [ "$state" != running ] && break
done
"${scp[@]}" "$name:~/round.log" "$LOGS/round.log" || true
"${scp[@]}" "$name:~/results.tgz" "$LOGS/results.tgz" || { echo "no results copied" >&2; exit 1; }
tar xzf "$LOGS/results.tgz" -C "$LOGS"
echo "=== done (state $state); results in $LOGS/mpout"
