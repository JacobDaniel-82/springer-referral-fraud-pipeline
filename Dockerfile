# Use official Python image (slim base keeps the image small)
FROM python:3.12-slim

# Set working directory inside the container
WORKDIR /app

# Copy requirements file first so Docker can cache this layer
# separately from the application code (faster rebuilds when only
# the script changes)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and source data into the container
COPY data_profiling.py referral_fraud_pipeline.py ./
COPY data/ ./data/

# The report is written to /app/output inside the container.
# We mount a host directory to this path at `docker run` time
# (see README.md) so the report ends up on the host machine,
# outside the container, as required by the spec.
RUN mkdir -p /app/output

# Default command: run profiling first, then the main pipeline.
# Both scripts write their CSV outputs to /app/output.
CMD ["sh", "-c", "python data_profiling.py && python referral_fraud_pipeline.py"]
