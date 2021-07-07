FROM debian:buster

RUN apt-get update \
 && apt-get install -y \
    atheme-services

COPY atheme.conf /etc/atheme/atheme.conf
COPY initial.db /var/lib/atheme/services.db

# HTTP port for XMLRPC
EXPOSE 8080
# Volume for services database
VOLUME /var/lib/atheme
ENTRYPOINT ["atheme-services", "-n", "-p", "/var/run/atheme.pid"]
