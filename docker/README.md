
### 1. Build the code server with extra dependencies

```bash
docker build -t my-code-server:0.1 -f Dockerfile.code_server .
```


### 2. Build the proxy interception container

```bash
docker build -t my-proxy-server:0.1 -f Dockerfile.proxy_server .
```

### 3. Build the mcp server container

```bash
docker build -t my-mcp-server:0.1 -f Dockerfile.mcp_server .
```