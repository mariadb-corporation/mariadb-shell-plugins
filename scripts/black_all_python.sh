#!/usr/bin/env bash
# Copyright (c) 2020, 2026, Oracle and/or its affiliates.

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

python_files=()
while IFS= read -r -d '' file; do
  python_files+=("$file")
done < <(git ls-files -z '*.py')

if (( ${#python_files[@]} == 0 )); then
  echo "No Python files found."
  exit 0
fi

black "${python_files[@]}"
