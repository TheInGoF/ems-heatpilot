ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY heatpilot ./heatpilot

CMD ["python3", "-m", "heatpilot.main"]
