FROM apache/airflow:2.2.1-python3.8
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN pip install --upgrade apache-airflow-providers-google

# docker login
# docker build . --tag viniciuscampos-airflow
# docker tag viniciuscampos-airflow:latest viniciuscamposdataengineer/viniciuscampos-airflow
# docker push viniciuscamposdataengineer/viniciuscampos-airflow
# docker run -p 8080:8080 viniciuscamposdataengineer/viniciuscampos-airflow:latest -it sh