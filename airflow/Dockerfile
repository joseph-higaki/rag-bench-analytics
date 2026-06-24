FROM apache/airflow:2.10.5-python3.12

# dbt in an isolated virtualenv — avoids dependency conflicts with Airflow.
RUN python -m venv /opt/airflow/dbt_venv \
    && /opt/airflow/dbt_venv/bin/pip install --no-cache-dir dbt-postgres==1.9.0

# Cosmos is an Airflow provider; it must live in the Airflow env.
RUN pip install --no-cache-dir "astronomer-cosmos==1.14.2"

# The extract_load PythonOperator imports the ingestion package, which uses psycopg3
# (distinct from dbt's psycopg2). boto3 + python-dotenv already ship in the base image;
# psycopg3 does not. Pin to the project's core dep. (`ingestion` itself is mounted at
# /opt/airflow/ingestion and put on PYTHONPATH via docker-compose.)
RUN pip install --no-cache-dir "psycopg[binary]==3.2.10"
