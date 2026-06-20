FROM apache/airflow:2.10.5-python3.12

# dbt in an isolated virtualenv — avoids dependency conflicts with Airflow.
RUN python -m venv /opt/airflow/dbt_venv \
    && /opt/airflow/dbt_venv/bin/pip install --no-cache-dir dbt-postgres==1.9.0

# Cosmos is an Airflow provider; it must live in the Airflow env.
RUN pip install --no-cache-dir "astronomer-cosmos==1.14.2"
