# ---------------------------------------------------------------- base
# python:3.12-slim: pandas tem wheel pronto pra manylinux, então não precisa
# de toolchain de compilação — a imagem fica em ~350MB em vez de ~1.2GB.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements primeiro: a layer de dependências só reconstrói quando o
# requirements.txt muda, não a cada edição de código.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ./core/
COPY api/ ./api/
COPY config/ ./config/
COPY main.py categorize.py ./

# ---------------------------------------------------------------- testes
# Estágio dedicado: `docker build` FALHA se a suíte falhar, então uma
# regressão nunca chega a virar imagem. Os testes são herméticos (geram os
# próprios extratos), o que importa aqui porque `input/` está no .dockerignore.
FROM base AS test

COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY tests/ ./tests/
RUN python -m pytest tests/ -q && touch /tests-passed

# ---------------------------------------------------------------- runtime
FROM base AS runtime

# Este COPY é o que obriga o estágio de teste a rodar: sem uma dependência
# entre estágios, o BuildKit pularia `test` por não ser alcançável a partir do
# alvo. O arquivo só existe se o pytest passou.
COPY --from=test /tests-passed /tests-passed

RUN useradd --create-home --uid 10001 fatura \
    && mkdir -p /data \
    && chown -R fatura:fatura /app /data
USER fatura

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

# Um worker só: o estado transacional está no SQLite, mas o WAL não gosta de
# muitos escritores concorrentes e isto aqui é uso pessoal. Se um dia precisar
# escalar, troque o SQLite por Postgres ANTES de subir workers.
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
