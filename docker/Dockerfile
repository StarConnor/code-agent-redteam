FROM codercom/code-server:latest

COPY node-v22.21.1-linux-x64/bin/ /usr/local/bin/
COPY node-v22.21.1-linux-x64/lib/ /usr/local/lib/
COPY node-v22.21.1-linux-x64/share/ /usr/local/share/
COPY node-v22.21.1-linux-x64/include/ /usr/local/include/

# Ensure binary location prioritized over older Node versions
ENV PATH="/usr/local/bin:${PATH}"
ENV HTTPS_PROXY="http://172.31.176.1:7897"

USER root
RUN npm install -g @mermaid-js/mermaid-cli
USER coder

ENV HTTPS_PROXY=""