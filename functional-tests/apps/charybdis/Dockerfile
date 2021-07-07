FROM debian:buster

RUN apt-get update \
 && apt-get install -y \
    charybdis \
    netbase
# netbase is needed for /etc/services and /etc/protocols

COPY ircd.conf /etc/charybdis/ircd.conf
EXPOSE 6667
USER charybdis
VOLUME /var/log/charybdis
ENTRYPOINT ["charybdis", "-foreground"]
