#!/bin/bash
# 本机执行。--plan 只生成已跟踪源码归档与计划，不联系目标机。
set -euo pipefail
cd "$(dirname "$0")/.."
tag="${1:?用法: scripts/deploy_232.sh v3.2.2 [--plan]}"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo '必须使用版本标签'; exit 2; }
git rev-parse --verify "refs/tags/$tag" >/dev/null
remote="${COST_DEPLOY_SSH:-${USER}@192.168.3.232}"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
# 只取标签内容，绝不取工作区或未跟踪的真实数据。
git archive "$tag" | tar -x -C "$stage"
rm -rf "$stage/design" "$stage/data" "$stage/dist" "$stage/runtime-mac" "$stage/runtime-windows" "$stage/协作" "$stage/outputs"
version="$(sed -n 's/^VERSION = "\([^"]*\)"/\1/p' "$stage/v2/version.py")"
[[ "v$version" == "$tag" ]] || { echo '标签与应用版本不一致'; exit 2; }
if [[ "${2:-}" == '--plan' ]]; then
  printf '计划：标签 %s -> %s:~/cost-studio/releases/%s\n' "$tag" "$remote" "$tag"
  printf '先安装依赖并通过 import 自检，再备份现有数据库、切换 current、重启和核对版本。失败恢复旧链接及迁移前数据库。\n'
  find "$stage" -type f | sed "s|$stage/||" | sort
  exit 0
fi
# 防止覆盖已存在 release；SSH 使用已知主机校验，不关闭 HostKeyChecking。
ssh "$remote" "test ! -e \"\$HOME/cost-studio/releases/$tag\" && mkdir -p \"\$HOME/cost-studio/releases/$tag\""
rsync -a "$stage/" "$remote:cost-studio/releases/$tag/"
# 目标机 sudo 验证只在明确运行发布时发生；程序路径/标签均为固定或经过校验的值。
ssh -t "$remote" "python_script=\"\$HOME/cost-studio/releases/$tag/scripts/activate_release.py\"; sudo -v && /usr/bin/python3 \"\$python_script\" '$tag'"
