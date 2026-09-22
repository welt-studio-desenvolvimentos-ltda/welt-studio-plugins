"""Git: raiz do repo, baseline no momento da aprovação e arquivos mudados desde então."""

import os
import subprocess

GIT_TIMEOUT = 15
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _git(root, *args):
    proc = subprocess.run(["git", "-C", root] + list(args), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=GIT_TIMEOUT, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return None
    return proc.stdout


def repo_root(cwd):
    """Raiz do repo git que contém `cwd`; fora de um repo, o próprio `cwd`."""
    try:
        out = _git(cwd, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.TimeoutExpired):
        out = None
    return os.path.realpath(out.strip()) if out else os.path.realpath(cwd)


def is_git(root):
    try:
        return _git(root, "rev-parse", "--git-dir") is not None
    except (OSError, subprocess.TimeoutExpired):
        return False


def _lines(out):
    return [line for line in (out or "").splitlines() if line]


def _dirty_files(root):
    """Arquivos com mudança em relação ao HEAD (rastreados) mais os não rastreados e não ignorados."""
    head = "HEAD" if _git(root, "rev-parse", "--verify", "-q", "HEAD") else EMPTY_TREE
    tracked = _lines(_git(root, "diff", "--name-only", head))
    untracked = _lines(_git(root, "ls-files", "--others", "--exclude-standard"))
    return sorted(set(tracked) | set(untracked))


def _hash(root, rel):
    path = os.path.join(root, rel)
    if not os.path.lexists(path):
        return None
    out = _git(root, "hash-object", "--no-filters", "--", rel)
    return out.strip() if out else None


def snapshot(root):
    """Baseline: o HEAD e o conteúdo dos arquivos que já estavam sujos antes da rota."""
    if not is_git(root):
        return None
    head = _git(root, "rev-parse", "--verify", "-q", "HEAD")
    return {
        "head": head.strip() if head else None,
        "dirty": {rel: _hash(root, rel) for rel in _dirty_files(root)},
    }


def changed_since(root, baseline):
    """Arquivos alterados desde a baseline, incluindo commits feitos no meio do caminho.

    Um arquivo que já estava sujo na baseline só conta se o conteúdo mudou desde então.
    """
    if not baseline or not is_git(root):
        return []
    # Repo sem commits na baseline: compara com a árvore vazia.
    head = baseline.get("head") or EMPTY_TREE
    tracked = _lines(_git(root, "diff", "--name-only", head))
    untracked = _lines(_git(root, "ls-files", "--others", "--exclude-standard"))
    dirty_before = baseline.get("dirty", {})
    changed = []
    for rel in sorted(set(tracked) | set(untracked) | set(dirty_before)):
        if rel in dirty_before and _hash(root, rel) == dirty_before[rel]:
            continue
        changed.append(rel)
    return changed


def is_tracked(root, rel):
    """Se o git conhece o caminho (arquivo, ou diretório com algo rastreado dentro)."""
    try:
        return bool(_lines(_git(root, "ls-files", "--", rel)))
    except (OSError, subprocess.TimeoutExpired):
        return True  # na dúvida, trata como rastreado: não libera remoção


def is_ignored(root, rel):
    """Se o `.gitignore` cobre o caminho: o que é ignorado nunca aparece no diff do Stop."""
    try:
        return _git(root, "check-ignore", "-q", "--", rel) is not None
    except (OSError, subprocess.TimeoutExpired):
        return True  # na dúvida, trata como ignorado: não libera remoção


def relative_to(root, path, cwd):
    """Caminho relativo à raiz, ou None quando fica fora dela."""
    absolute = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))
    root = os.path.realpath(root)
    if absolute != root and not absolute.startswith(root + os.sep):
        return None
    return os.path.relpath(absolute, root).replace(os.sep, "/")
