FROM apache/airflow:2.2.1-python3.8

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    sshpass \
    && apt-get autoremove -yqq --purge \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
USER airflow
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# docker login
# docker build . --tag viniciuscampos-airflow
# docker tag viniciuscampos-airflow:latest viniciuscamposdataengineer/viniciuscampos-airflow
# docker push viniciuscamposdataengineer/viniciuscampos-airflow
# docker run -p 8080:8080 viniciuscamposdataengineer/viniciuscampos-airflow:latest webserver