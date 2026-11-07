FROM alpine:3.24
ARG REBUILD=0
RUN apk add --no-cache bird
EXPOSE 179
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD birdc show status >/dev/null || exit 1
CMD ["bird", "-f", "-c", "/etc/bird/bird.conf"]
