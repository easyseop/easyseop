#!/bin/bash
# Claude 가 푸시한 변경 사항을 새 meetcute 레포로 동기화.
#
# 흐름:
#   1. 부모 ~/easyseop/.git 에서 claude 브랜치 pull (Claude 가 푸시한 거 받기)
#   2. 변경된 파일들이 meetcute/ 폴더 안에 떨어짐
#   3. 이 폴더의 nested .git 으로 add + commit + push → easyseop/meetcute:main
#
# 사용:
#   cd ~/easyseop/meetcute
#   ./sync-from-upstream.sh
#
# 변경 없으면 "변경 없음" 출력하고 끝.

set -e

HERE=$(cd "$(dirname "$0")" && pwd)
PARENT=$(dirname "$HERE")
UPSTREAM_BRANCH="${MEETCUTE_UPSTREAM_BRANCH:-claude/add-claude-documentation-yKRhW}"

# 부모 디렉토리가 git 레포인지 확인
if [ ! -d "$PARENT/.git" ]; then
  echo "❌ 부모 ($PARENT) 가 git 레포 아님. easyseop/easyseop 체크아웃 위치여야 합니다."
  exit 1
fi

# 현재 폴더가 git 레포인지 확인 (meetcute 자체 .git)
if [ ! -d "$HERE/.git" ]; then
  echo "❌ 현재 폴더 ($HERE) 가 git 레포 아님. 'git init' 후 origin 연결됐어야 합니다."
  exit 1
fi

echo "==> 1) 부모 레포 ($PARENT) 에서 $UPSTREAM_BRANCH pull..."
cd "$PARENT"
# 첫 사용 시 origin 이 없을 수 있어 fetch 부터
git fetch origin "$UPSTREAM_BRANCH"
# 부모 레포가 다른 브랜치에 있을 수 있어 명시적으로 merge
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "$UPSTREAM_BRANCH" ]; then
  echo "   부모가 $CURRENT_BRANCH 에 있어 $UPSTREAM_BRANCH 로 checkout..."
  git checkout "$UPSTREAM_BRANCH"
fi
git pull origin "$UPSTREAM_BRANCH"

echo ""
echo "==> 2) meetcute 자체 레포로 동기화..."
cd "$HERE"
git add -A

if git diff --staged --quiet; then
  echo "✅ 변경 없음 — 이미 최신 상태입니다."
  exit 0
fi

# 어떤 파일이 변경됐는지 요약
echo "변경된 파일:"
git diff --staged --stat | tail -20

# 커밋 + 푸시
COMMIT_MSG="sync from upstream $(date +%Y-%m-%d_%H:%M)"
git commit -m "$COMMIT_MSG"
echo ""
echo "==> 3) easyseop/meetcute 로 푸시..."
git push origin main
echo ""
echo "✅ 동기화 완료. github.com/easyseop/meetcute 에서 확인 가능."
