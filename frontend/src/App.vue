<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">洞察工坊 · 多维交互分析与实验度量平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向数据分析师的多维下钻分析、指标口径管理、A/B实验评估、群体圈选分群、归因分析与数据看板构建的交互分析平台。</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "收入观测证据", path: "/income" }, { label: "指标定义", path: "/metric_def" }, { label: "维度定义", path: "/dimension_def" }, { label: "多维查询", path: "/olap_query" }, { label: "群体圈选", path: "/segment_builder" }, { label: "A/B实验", path: "/ab_experiment" }, { label: "实验分析", path: "/experiment_result" }, { label: "归因分析", path: "/attribution_model" }, { label: "看板定义", path: "/dashboard_def" }, { label: "下钻路径", path: "/drill_path" }, { label: "同期群分析", path: "/cohort_analysis" }, { label: "数据导出", path: "/data_export" }, { label: "查询缓存", path: "/query_cache" }, { label: "定时分析", path: "/scheduled_analysis" }, { label: "指标对比", path: "/metric_compare" }, { label: "异常检测", path: "/anomaly_detect" }, { label: "分析快照", path: "/data_snapshot" }, { label: "分群比较", path: "/user_segment" }, { label: "事件追踪", path: "/event_track" }, { label: "排行榜", path: "/ranking_board" }, { label: "漏斗分析", path: "/funnel_def" }, { label: "数据集关联", path: "/dataset_join" }, { label: "报告生成", path: "/report_builder" }]
</script>
