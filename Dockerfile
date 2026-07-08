# 用途: FastAPIまたは各MCPサーバーを動かす共通Pythonコンテナを作る。
# 必要な理由: 開発機と本番環境のPython・依存バージョンを揃え、非rootユーザーで安全に動かすため。
# 関連ファイル: pyproject.tomlの依存を導入し、docker-compose.ymlからAPI/MCPごとに別commandで起動する。


FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .
COPY . .

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8787
CMD ["python", "main.py"]
