FROM python:3.11
WORKDIR /app
COPY ./books_database/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
CMD python utils/other/hotreload.py "books_database/src/app.py"
