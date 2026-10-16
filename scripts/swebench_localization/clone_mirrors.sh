#!/bin/bash
# Bare mirrors of the 12 repositories SWE-bench Verified draws from (branches and tags, full history).
# Usage: clone_mirrors.sh [directory]   (default: $SWEBENCH_MIRRORS or ~/.cache/spine-swebench/mirrors)
# About 1.7 GB. Safe to re-run: a mirror that already exists is skipped.
set -u
dir="${1:-${SWEBENCH_MIRRORS:-$HOME/.cache/spine-swebench/mirrors}}"
mkdir -p "$dir" && cd "$dir" || exit 1
for r in django/django sympy/sympy sphinx-doc/sphinx matplotlib/matplotlib scikit-learn/scikit-learn \
         astropy/astropy pydata/xarray pytest-dev/pytest pylint-dev/pylint psf/requests mwaskom/seaborn \
         pallets/flask; do
  d="${r//\//__}.git"
  [ -d "$d" ] && { echo "have $d"; continue; }
  echo "$(date +%T) start $r"
  if git clone --bare --quiet "https://github.com/$r.git" "$d.partial" && mv "$d.partial" "$d"; then
    echo "$(date +%T) done $r $(du -sh "$d" | cut -f1)"
  else
    echo "FAILED $r"
  fi
done
echo CLONEDONE
