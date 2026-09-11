package com.mogan.earth.geomcp;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * GeoMCP 薄客户端 — Java 与 Python 执行面的唯一通信桥梁。
 *
 * <h3>使用示例</h3>
 * <pre>{@code
 * GeoMCPClient client = new GeoMCPClient("http://127.0.0.1:8787", "my-api-key");
 *
 * // 查询能力
 * Map<String, Object> caps = client.capabilities();
 *
 * // 执行 NDVI
 * ExecuteResult result = client.execute(
 *     ExecuteRequest.of("ndvi-analysis")
 *         .geocards(List.of("amazon-ndvi-2015"))
 *         .spatial(new SpatialContext(List.of(-73.9, -15.0, -44.0, 5.0), "EPSG:4326"))
 *         .temporal(new TemporalContext("2015-01-01", "2015-12-31"))
 *         .params(Map.of("red", "/data/red.tif", "nir", "/data/nir.tif"))
 * );
 *
 * Map<String, Object> stats = (Map<String, Object>) result.getOutputs().get("stats");
 * }</pre>
 *
 * <p>依赖：仅需 JDK 11+ 内置的 {@code java.net.http.HttpClient} 和 Jackson。</p>
 * <p>线程安全：本类无状态，可在多线程中共享单例。</p>
 */
public class GeoMCPClient {

    private static final String JSONRPC = "2.0";
    private static final Duration DEFAULT_TIMEOUT = Duration.ofSeconds(30);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final String baseUrl;
    private final String apiKey;
    private final HttpClient http;
    private final Duration timeout;

    // ── 构造 ──

    public GeoMCPClient(String baseUrl) {
        this(baseUrl, null, DEFAULT_TIMEOUT);
    }

    public GeoMCPClient(String baseUrl, String apiKey) {
        this(baseUrl, apiKey, DEFAULT_TIMEOUT);
    }

    public GeoMCPClient(String baseUrl, String apiKey, Duration timeout) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.apiKey = apiKey;
        this.timeout = timeout;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
    }

    // ── 四个核心方法 ──

    /**
     * 查询节点能力：协议版本、方法列表、技能、GeoCard 等。
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> capabilities() {
        JsonRpcResponse resp = dispatch("geo.capabilities", Map.of());
        return (Map<String, Object>) resp.getResult();
    }

    /**
     * 查询指定的 GeoCard 和 Skill 详情。
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> describe(List<String> geocards, List<String> skills) {
        Map<String, Object> params = Map.of(
                "geocards", geocards != null ? geocards : List.of(),
                "skills", skills != null ? skills : List.of()
        );
        JsonRpcResponse resp = dispatch("geo.describe", params);
        return (Map<String, Object>) resp.getResult();
    }

    /**
     * 执行技能，返回强类型 ExecuteResult。
     */
    public ExecuteResult execute(ExecuteRequest req) {
        JsonRpcResponse resp = dispatch("geo.execute", req);
        return MAPPER.convertValue(resp.getResult(), ExecuteResult.class);
    }

    /**
     * 健康检查（JSON-RPC 方式）。
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> healthRpc() {
        JsonRpcResponse resp = dispatch("geo.health", Map.of());
        return (Map<String, Object>) resp.getResult();
    }

    /**
     * 健康检查（HTTP GET 方式，不使用 JSON-RPC 信封）。
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> health() {
        try {
            String body = get("/health");
            return MAPPER.readValue(body, Map.class);
        } catch (IOException e) {
            throw new GeoMCPClientException("Failed to parse health response", e);
        }
    }

    // ── 内部实现 ──

    private JsonRpcResponse dispatch(String method, Object params) {
        String id = "req-" + UUID.randomUUID().toString().substring(0, 8);
        JsonRpcRequest request = new JsonRpcRequest(id, method, params);

        try {
            String reqBody = MAPPER.writeValueAsString(request);
            String respBody = post("/geomcp", reqBody);
            JsonRpcResponse resp = MAPPER.readValue(respBody, JsonRpcResponse.class);

            if (resp.getError() != null) {
                JsonRpcError err = resp.getError();
                throw new GeoMCPClientException(err.getCode(), err.getMessage());
            }
            return resp;

        } catch (JsonProcessingException e) {
            throw new GeoMCPClientException(-32700, "Failed to serialize request", e);
        } catch (GeoMCPClientException e) {
            throw e; // 重新抛出
        } catch (IOException e) {
            throw new GeoMCPClientException("I/O error calling GeoMCP: " + e.getMessage(), e);
        }
    }

    private String post(String path, String body) throws IOException {
        var reqBuilder = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + path))
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body));

        if (apiKey != null) {
            reqBuilder.header("X-API-Key", apiKey);
        }

        try {
            HttpResponse<String> resp = http.send(reqBuilder.build(),
                    HttpResponse.BodyHandlers.ofString());
            return resp.body();
        } catch (HttpTimeoutException e) {
            throw new GeoMCPClientException("GeoMCP request timed out after " + timeout.getSeconds() + "s", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new GeoMCPClientException("GeoMCP request interrupted", e);
        }
    }

    private String get(String path) throws IOException {
        var reqBuilder = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + path))
                .timeout(timeout)
                .GET();

        if (apiKey != null) {
            reqBuilder.header("X-API-Key", apiKey);
        }

        try {
            HttpResponse<String> resp = http.send(reqBuilder.build(),
                    HttpResponse.BodyHandlers.ofString());
            return resp.body();
        } catch (HttpTimeoutException e) {
            throw new GeoMCPClientException("GeoMCP health check timed out", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new GeoMCPClientException("GeoMCP health check interrupted", e);
        }
    }
}