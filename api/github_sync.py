"""Publicação do `categories.yml` no GitHub.

SEGURANÇA DO TOKEN
------------------
* O token é um **fine-grained PAT** com escopo `Contents: Read and write`
  restrito a UM repositório. Não é um classic token com `repo` inteiro.
* Chega por variável de ambiente (`FATURA_GITHUB_TOKEN`), carregada de um
  arquivo `.env` que fica fora da imagem e fora do git, ou de um Docker
  secret montado em `/run/secrets`. Nunca é `ARG`/`ENV` no Dockerfile — isso
  ficaria gravado numa layer e vazaria em `docker history`.
* No processo ele vive como `SecretStr`, então não aparece em log, traceback,
  `repr()` nem em resposta de endpoint.
* Nenhum endpoint devolve, ecoa ou aceita o token.

CONCORRÊNCIA
------------
O SHA do arquivo é lido no /upload e guardado na transação. Na hora do commit,
comparamos com o SHA atual do repositório: se você editou o `categories.yml`
no seu Mac e deu push durante a revisão, o commit é recusado com 409 em vez de
sobrescrever seu trabalho.

COMENTÁRIOS DO YAML
-------------------
O conteúdo enviado é sempre o texto produzido por `core.yaml_edit`, que insere
linhas no arquivo original. Nunca um `yaml.dump`, que apagaria todos os seus
comentários e reordenaria o arquivo.
"""

from __future__ import annotations

from dataclasses import dataclass

from .settings import Settings


class GitHubDisabled(RuntimeError):
    pass


class GitHubConflict(RuntimeError):
    def __init__(self, expected: str | None, actual: str | None):
        super().__init__(
            "o categories.yml mudou no repositório desde o upload "
            f"(esperado {expected!r}, atual {actual!r})"
        )
        self.expected = expected
        self.actual = actual


@dataclass
class RemoteFile:
    text: str
    sha: str


class GitHubSync:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _repo(self):
        if not self.settings.github_enabled:
            raise GitHubDisabled("FATURA_GITHUB_TOKEN não configurado")
        from github import Auth, Github  # import tardio: opcional em dev

        auth = Auth.Token(self.settings.github_token.get_secret_value())
        return Github(auth=auth).get_repo(self.settings.github_repo)

    def fetch(self) -> RemoteFile:
        contents = self._repo().get_contents(
            self.settings.github_file_path, ref=self.settings.github_branch
        )
        return RemoteFile(text=contents.decoded_content.decode("utf-8"), sha=contents.sha)

    def current_sha(self) -> str | None:
        try:
            return self.fetch().sha
        except Exception:
            return None

    def commit(self, text: str, message: str, expected_sha: str | None) -> str:
        repo = self._repo()
        contents = repo.get_contents(
            self.settings.github_file_path, ref=self.settings.github_branch
        )
        if expected_sha and contents.sha != expected_sha:
            raise GitHubConflict(expected_sha, contents.sha)

        from github import InputGitAuthor

        author = InputGitAuthor(
            self.settings.github_author_name, self.settings.github_author_email
        )
        result = repo.update_file(
            path=self.settings.github_file_path,
            message=message,
            content=text,
            sha=contents.sha,
            branch=self.settings.github_branch,
            author=author,
            committer=author,
        )
        return result["commit"].html_url


def commit_message(changes: list[dict], period: str) -> str:
    """Uma mensagem por sessão, resumindo o que foi decidido."""
    if not changes:
        return f"mapeamento: revisão da fatura {period}"

    counts: dict[str, int] = {}
    for change in changes:
        counts[change["kind"]] = counts.get(change["kind"], 0) + 1

    label = {
        "keyword": "palavra(s)-chave",
        "unknown": "desconhecido(s)",
        "marketplace": "marketplace(s)",
        "category": "categoria(s)",
    }
    summary = ", ".join(f"+{n} {label.get(kind, kind)}" for kind, n in sorted(counts.items()))

    body = "\n".join(
        f"- {c['kind']}: {c['value']}" + (f" -> {c['categoria']}" if c.get("categoria") else "")
        for c in changes
    )
    return f"mapeamento: {summary} (fatura {period})\n\n{body}\n"
