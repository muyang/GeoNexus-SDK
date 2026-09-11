# GeoNexus GeoMCP Java Client

> 零 Python 依赖的 GeoMCP 薄客户端。Java 控制面与 Python 执行面之间的唯一通信桥梁。

## 依赖

- **JDK 11+**（使用内置 `java.net.http.HttpClient`）
- **Jackson**（Spring Boot 项目自带，无需额外添加）

## 快速使用

```java
// 1. 创建客户端（单例，线程安全）
GeoMCPClient client = new GeoMCPClient("http://127.0.0.1:8787", "my-api-key");

// 2. 查询能力
Map<String, Object> caps = client.capabilities();
// → {protocol: "geomcp", version: "1.0.0", methods: [...], skills: [...]}

// 3. 执行 NDVI
ExecuteResult result = client.execute(
    ExecuteRequest.of("ndvi-analysis")
        .geocards(List.of("amazon-ndvi-2015"))
        .spatial(new SpatialContext(
            List.of(-73.9, -15.0, -44.0, 5.0), "EPSG:4326"))
        .temporal(new TemporalContext("2015-01-01", "2015-12-31"))
        .params(Map.of("red", "/data/red.tif", "nir", "/data/nir.tif"))
);

// 4. 获取结果
Map<String, Object> stats = (Map<String, Object>) result.getOutputs().get("stats");
double mean = (Double) stats.get("mean"); // 0.749

// 5. 健康检查
Map<String, Object> health = client.health();
// → {status: "ok", checks: {registry: "ok", oge_connectivity: "ok", ...}}
```

## 错误处理

```java
try {
    client.execute(ExecuteRequest.of("unknown-skill"));
} catch (GeoMCPClientException e) {
    if (e.isSkillNotFound()) {
        // error 2001 — 技能不存在
    } else if (e.isContractNotSatisfied()) {
        // error 2000 — 契约校验失败（bbox/CRS/时间不匹配）
    } else if (e.isExecutionFailed()) {
        // error 2003 — 执行异常
    }
}
```

## 独立开发（无需 Python 环境）

```bash
# 设置契约目录路径（本仓库内的 geonexus-contracts/）
export GEONEXUS_CONTRACTS=./geonexus-contracts

# 运行集成测试（WireMock 加载 mock 响应）
mvn test
```

## 文件结构

```
java-client/
├── pom.xml
└── src/
    ├── main/java/com/mogan/earth/geomcp/
    │   ├── GeoMCPClient.java              ← 核心客户端（~130 行）
    │   ├── GeoMCPClientException.java     ← 异常类
    │   ├── JsonRpcRequest.java            ← JSON-RPC 请求信封
    │   ├── JsonRpcResponse.java           ← JSON-RPC 响应信封
    │   ├── JsonRpcError.java              ← JSON-RPC 错误对象
    │   ├── ExecuteRequest.java            ← geo.execute 请求
    │   ├── ExecuteResult.java             ← geo.execute 响应
    │   ├── SpatialContext.java            ← 空间上下文
    │   └── TemporalContext.java           ← 时间上下文
    └── test/java/com/mogan/earth/geomcp/
        └── GeoMCPClientTest.java          ← WireMock 集成测试
```

## 协议版本

| GeoMCP 协议 | 客户端版本 | 状态 |
|------------|-----------|------|
| 1.0.0 | 1.0.0 | 当前 |

## 契约管理（Java 拥有）

契约文件（`geonexus-contracts/`：OpenAPI 规范 + Mock 响应 + 一致性测试向量）
**由 Java 团队（mgbackend 仓库）拥有并维护**：

- 契约变更：修改本仓库的 `geonexus-contracts/` → CI 自动验证 → 合并。
- 通知 Python 团队：通过 PR 告知 SDK 仓库，由 Python 团队确认 SDK 的
  GeoMCP 实现是否兼容，并同步更新 SDK 的 `tests/conformance/`。
- Python 团队不直接修改契约文件；其实现正确性由 SDK 的
  `tests/conformance/` 向量独立保证。
- 集成时若发现不一致，Java 侧契约与 Python 侧实现在各自的 CI 中修复对齐。