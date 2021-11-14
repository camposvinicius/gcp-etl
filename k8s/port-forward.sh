kubectl port-forward svc/argocd-server -n argocd 8181:443 &&
kubectl port-forward svc/airflow-web -n airflow 8080:8080