package com.mogan.earth.geomcp;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.tomakehurst.wiremock.junit5.WireMockExtension;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;

import static com.github.tomakehurst.wiremock.client.WireMock.*;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.wireMockConfig;
import static org.assertj.core.api.Assertions.*;

/**
 * GeoMCP 客户端集成测试 — 使用 WireMock 加载共享契约仓库的 mock 响应。
 *
 * <p>运行此测试不需要 Python 环境，只需 {@code geonexus-contracts/mocks/} 目录。
 * 这验证了 Java 团队可以完全独立开发。
 *
 * <p>使用方法：
 * <pre>{@code
 * # 契约目录默认自动定位（仓库内 ./geonexus-contracts 或旁目录 ../geonexus-contracts）
 * # 也可通过环境变量显式指定：
 * export GEONEXUS_CONTRACTS=./geonexus-contracts
 * mvn test
 * }</pre>
 */
class GeoMCPClientTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /**
     * 定位 geonexus-contracts 目录（Java 团队拥有，位于本仓库内）。
     *
     * 优先级：
     *   1. 环境变量 GEONEXUS_CONTRACTS
     *   2. 仓库内 ./geonexus-contracts（契约并入 mgbackend/java-client 后的布局）
     *   3. 旁目录 ../geonexus-contracts（当前工作区布局）
     */
    private static final String CONTRACTS_DIR = resolveContractsDir();

    private static String resolveContractsDir() {
        String fromEnv = System.getenv().get("GEONEXUS_CONTRACTS");
        if (fromEnv != null && !fromEnv.isBlank()) {
            return fromEnv;
        }
        Path inRepo = Paths.get("geonexus-contracts");
        if (Files.isDirectory(inRepo)) {
            return inRepo.toString();
        }
        Path sibling = Paths.get("..", "geonexus-contracts");
        if (Files.isDirectory(sibling)) {
            return sibling.toString();
        }
        throw new IllegalStateException(
            "Cannot locate geonexus-contracts/. Set GEONEXUS_CONTRACTS env var.");
    }

    private static String loadMock(String relativePath) {
        try {
            Path path = Paths.get(CONTRACTS_DIR, relativePath);
            return Files.readString(path);
        } catch (IOException e) {
            throw new RuntimeException("Failed to load mock: " + relativePath +
                    "\n  Set GEONEXUS_CONTRACTS env var to the contracts repo path.", e);
        }
    }

    @RegisterExtension
    static WireMockExtension wm = WireMockExtension.newInstance()
            .options(wireMockConfig().dynamicPort())
            .build();

    private GeoMCPClient client;

    @BeforeEach
    void setUp() {
        client = new GeoMCPClient("http://127.0.0.1:" + wm.getPort(), "test-api-key");
    }

    // ──────────────────────────────────────────────
    // 成功路径
    // ──────────────────────────────────────────────

    @Test
    void shouldReturnCapabilities() {
        // given
        wm.stubFor(post("/geomcp")
                .withRequestBody(matchingJsonPath("$.method", equalTo("geo.capabilities")))
                .willReturn(aResponse()
                        .withHeader("Content-Type", "application/json")
                        .withBody(loadMock("mocks/capabilities.json"))));

        // when
        Map<String, Object> caps = client.capabilities();

        // then
        assertThat(caps.get("protocol")).isEqualTo("geomcp");
        assertThat(caps.get("version")).isEqualTo("1.0.0");
        assertThat(caps.get("node")).isEqualTo("execution-plane-mock");

        @SuppressWarnings("unchecked")
        List<String> methods = (List<String>) caps.get("methods");
        assertThat(methods).contains("geo.execute", "geo.capabilities", "geo.describe", "geo.health");

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> skills = (List<Map<String, Object>>) caps.get("skills");
        assertThat(skills).hasSize(3);
        assertThat(skills.get(0).get("name")).isEqualTo("ndvi-analysis");
    }

    @Test
    void shouldExecuteNDVI() {
        // given
        wm.stubFor(post("/geomcp")
                .withRequestBody(matchingJsonPath("$.method", equalTo("geo.execute")))
                .willReturn(aResponse()
                        .withHeader("Content-Type", "application/json")
                        .withBody(loadMock("mocks/execute-ndvi.json"))));

        // when
        ExecuteResult result = client.execute(
                ExecuteRequest.of("ndvi-analysis")
                        .geocards(List.of("amazon-ndvi-2015"))
                        .spatial(new SpatialContext(List.of(-73.9, -15.0, -44.0, 5.0), "EPSG:4326"))
                        .temporal(new TemporalContext("2015-01-01", "2015-12-31"))
                        .params(Map.of("red", "/data/red.tif", "nir", "/data/nir.tif"))
        );

        // then
        assertThat(result.isOk()).isTrue();
        assertThat(result.getSkill()).isEqualTo("ndvi-analysis");
        assertThat(result.getExecutedBy()).isEqualTo("data-node-a");

        @SuppressWarnings("unchecked")
        Map<String, Object> stats = (Map<String, Object>) result.getOutputs().get("stats");
        assertThat(stats).containsKeys("mean", "std", "min", "max");
        assertThat((Double) stats.get("mean")).isCloseTo(0.749, within(0.001));
    }

    @Test
    void shouldExecuteChangeDetection() {
        // given
        wm.stubFor(post("/geomcp")
                .withRequestBody(matchingJsonPath("$.method", equalTo("geo.execute")))
                .willReturn(aResponse()
                        .withHeader("Content-Type", "application/json")
                        .withBody(loadMock("mocks/execute-change.json"))));

        // when
        ExecuteResult result = client.execute(
                ExecuteRequest.of("ndvi-change")
                        .geocards(List.of("model-ndvi-change"))
                        .params(Map.of("ndvi_a", "/out/ndvi_2015.tif", "ndvi_b", "/out/ndvi_2025.tif"))
        );

        // then
        assertThat(result.isOk()).isTrue();
        assertThat(result.getExecutedBy()).isEqualTo("compute-node");

        @SuppressWarnings("unchecked")
        Map<String, Object> stats = (Map<String, Object>) result.getOutputs().get("stats");
        assertThat((Double) stats.get("mean_change")).isCloseTo(-0.430, within(0.001));
    }

    @Test
    void shouldReturnHealth() {
        // given
        wm.stubFor(get("/health")
                .willReturn(aResponse()
                        .withHeader("Content-Type", "application/json")
                        .withBody(loadMock("mocks/health.json"))));

        // when
        Map<String, Object> health = client.health();

        // then
        assertThat(health.get("status")).isEqualTo("ok");
        assertThat(health.get("node")).isEqualTo("execution-plane-mock");

        @SuppressWarnings("unchecked")
        Map<String, Object> checks = (Map<String, Object>) health.get("checks");
        assertThat(checks.get("registry")).isEqualTo("ok");
        assertThat(checks.get("skills_loaded")).isEqualTo(3);
    }

    // ──────────────────────────────────────────────
    // 错误路径
    // ──────────────────────────────────────────────

    @Test
    void shouldThrowOnContractNotSatisfied() {
        wm.stubFor(post("/geomcp")
                .withRequestBody(matchingJsonPath("$.method", equalTo("geo.execute")))
                .willReturn(aResponse()
                        .withHeader("Content-Type", "application/json")
                        .withBody(loadMock("mocks/errors/contract-not-satisfied.json"))));

        assertThatThrownBy(() -> client.execute(
                ExecuteRequest.of("ndvi-analysis")
                        .geocards(List.of("amazon-ndvi-2015"))
                        .spatial(new SpatialContext(List.of(100.0, 0.0, 110.0, 10.0), "EPSG:3857"))
        ))
                .isInstanceOf(GeoMCPClientException.class)
                .matches(e -> ((GeoMCPClientException) e).isContractNotSatisfied())
                .hasMessageContaining("Contract not satisfied");
    }

    @Test
    void shouldThrowOnSkillNotFound() {
        wm.stubFor(post("/geomcp")
                .withRequestBody(matchingJsonPath("$.method", equalTo("geo.execute")))
                .willReturn(aResponse()
                        .withHeader("Content-Type", "application/json")
                        .withBody(loadMock("mocks/errors/skill-not-found.json"))));

        assertThatThrownBy(() -> client.execute(ExecuteRequest.of("unknown-skill")))
                .isInstanceOf(GeoMCPClientException.class)
                .matches(e -> ((GeoMCPClientException) e).isSkillNotFound());
    }

    @Test
    void shouldThrowOnExecutionFailed() {
        wm.stubFor(post("/geomcp")
                .withRequestBody(matchingJsonPath("$.method", equalTo("geo.execute")))
                .willReturn(aResponse()
                        .withHeader("Content-Type", "application/json")
                        .withBody(loadMock("mocks/errors/execution-failed.json"))));

        assertThatThrownBy(() -> client.execute(
                ExecuteRequest.of("ndvi-analysis")
                        .geocards(List.of("amazon-ndvi-2015"))
                        .params(Map.of("red", "/data/missing.tif", "nir", "/data/nir.tif"))
        ))
                .isInstanceOf(GeoMCPClientException.class)
                .matches(e -> ((GeoMCPClientException) e).isExecutionFailed());
    }

    @Test
    void shouldThrowOnInvalidArgument() {
        wm.stubFor(post("/geomcp")
                .withRequestBody(matchingJsonPath("$.method", equalTo("geo.execute")))
                .willReturn(aResponse()
                        .withHeader("Content-Type", "application/json")
                        .withBody(loadMock("mocks/errors/invalid-argument.json"))));

        assertThatThrownBy(() -> client.execute(
                ExecuteRequest.of("ndvi-analysis")
                        .geocards(List.of("amazon-ndvi-2015"))
                        .params(Map.of("nir", "/data/nir.tif"))  // 缺少 red
        ))
                .isInstanceOf(GeoMCPClientException.class)
                .matches(e -> ((GeoMCPClientException) e).isInvalidArgument());
    }

    // ──────────────────────────────────────────────
    // 协议级错误
    // ──────────────────────────────────────────────

    @Test
    void shouldThrowOnTimeout() {
        // 模拟无响应的服务端
        GeoMCPClient slowClient = new GeoMCPClient(
                "http://127.0.0.1:" + wm.getPort(),
                null,
                java.time.Duration.ofMillis(100)
        );
        wm.stubFor(post("/geomcp")
                .willReturn(aResponse().withFixedDelay(5000)));

        assertThatThrownBy(() -> slowClient.capabilities())
                .isInstanceOf(GeoMCPClientException.class)
                .hasMessageContaining("timed out");
    }

    @Test
    void shouldForwardApiKey() {
        // given
        wm.stubFor(post("/geomcp")
                .withHeader("X-API-Key", equalTo("test-api-key"))
                .willReturn(aResponse()
                        .withHeader("Content-Type", "application/json")
                        .withBody(loadMock("mocks/capabilities.json"))));

        // when
        Map<String, Object> caps = client.capabilities();

        // then — 若 API Key 未传递，WireMock 不会匹配此 stub，测试会失败
        assertThat(caps.get("node")).isNotNull();
    }
}