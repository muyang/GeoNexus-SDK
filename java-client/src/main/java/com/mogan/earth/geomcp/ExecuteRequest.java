package com.mogan.earth.geomcp;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;
import java.util.Map;

/**
 * geo.execute 请求参数。
 * 业务代码构造此对象，传给 GeoMCPClient.execute()。
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class ExecuteRequest {

    /** 技能名称（必填） */
    private String skill;

    /** 引用的 GeoCard ID 列表 */
    private List<String> geocards;

    /** 空间上下文 */
    @JsonProperty("spatial")
    private SpatialContext spatial;

    /** 时间上下文 */
    @JsonProperty("temporal")
    private TemporalContext temporal;

    /** 技能参数 */
    private Map<String, Object> params;

    /** 全链路追踪 ID */
    @JsonProperty("request_id")
    private String requestId;

    public ExecuteRequest() {}

    /** 快捷构造：至少需要 skill */
    public static ExecuteRequest of(String skill) {
        ExecuteRequest r = new ExecuteRequest();
        r.skill = skill;
        return r;
    }

    // ── fluent setters ──

    public ExecuteRequest skill(String v)      { this.skill = v; return this; }
    public ExecuteRequest geocards(List<String> v) { this.geocards = v; return this; }
    public ExecuteRequest spatial(SpatialContext v) { this.spatial = v; return this; }
    public ExecuteRequest temporal(TemporalContext v) { this.temporal = v; return this; }
    public ExecuteRequest params(Map<String, Object> v) { this.params = v; return this; }
    public ExecuteRequest requestId(String v)  { this.requestId = v; return this; }

    // ── getters ──

    public String getSkill() { return skill; }
    public List<String> getGeocards() { return geocards; }
    public SpatialContext getSpatial() { return spatial; }
    public TemporalContext getTemporal() { return temporal; }
    public Map<String, Object> getParams() { return params; }
    public String getRequestId() { return requestId; }
}