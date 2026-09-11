package com.mogan.earth.geomcp;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;

/**
 * 空间上下文：bbox + CRS + 分辨率。
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class SpatialContext {

    /** [west, south, east, north] (4 元素) 或 3D 变体 */
    private List<Double> bbox;

    /** 坐标参考系，默认 EPSG:4326 */
    private String crs;

    /** 请求的空间分辨率（米） */
    private Double resolution;

    public SpatialContext() {}

    public SpatialContext(List<Double> bbox, String crs) {
        this.bbox = bbox;
        this.crs = crs;
    }

    public SpatialContext(List<Double> bbox, String crs, Double resolution) {
        this.bbox = bbox;
        this.crs = crs;
        this.resolution = resolution;
    }

    // ── getters / setters ──

    public List<Double> getBbox() { return bbox; }
    public void setBbox(List<Double> bbox) { this.bbox = bbox; }
    public String getCrs() { return crs; }
    public void setCrs(String crs) { this.crs = crs; }
    public Double getResolution() { return resolution; }
    public void setResolution(Double resolution) { this.resolution = resolution; }
}