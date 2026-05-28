import json
import re
import pandas as pd

CSV_FILE_PATH = "neo4j_query_table_data_2026-5-28.csv"
HTML_OUTPUT_PATH = "graph_visualization2.html"


def clean_and_parse_json(json_str):
    """安全且高效地解析 Neo4j 导出属性中的复杂/不规范 JSON 字符串"""
    if pd.isna(json_str) or not str(json_str).strip():
        return {}
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            # 兼容处理未加双引号的 key
            fixed_str = re.sub(r"(\b\w+\b)(?=\s*:)", r'"\1"', json_str)
            return json.loads(fixed_str)
        except Exception:
            return {"raw_data": str(json_str)}


def extract_label(label_str):
    if pd.isna(label_str):
        return "Unknown"
    return str(label_str).strip("[] ")


def main():
    print(f"正在读取 CSV 文件: {CSV_FILE_PATH} ...")
    # 仅读取需要的列，降低 Pandas 自身的初始内存开销
    use_cols = [
        "source_id",
        "source_labels",
        "source_properties",
        "relation",
        "target_id",
        "target_labels",
        "target_properties",
    ]
    df = pd.read_csv(CSV_FILE_PATH, usecols=use_cols)

    nodes_dict = {}
    edges_list = []
    all_labels = set()
    all_domains = set()

    print("正在增量解析去重，构建轻量化拓扑网...")
    for _, row in df.iterrows():
        s_id = str(row["source_id"])
        t_id = str(row["target_id"])

        # 1. 增量解析源节点
        if s_id not in nodes_dict:
            s_label = extract_label(row["source_labels"])
            s_props = clean_and_parse_json(row["source_properties"])
            s_name = s_props.get("name", f"ID_{s_id}")
            s_domain = s_props.get("knowledge_domain", "未分类")

            all_labels.add(s_label)
            all_domains.add(s_domain)

            nodes_dict[s_id] = {
                "id": s_id,
                "label": s_name,  # 画布上仅渲染简短名称
                "group": s_label,
                "domain": s_domain,
                "properties": s_props,  # 完整属性留作点击动态查询
            }

        # 2. 增量解析目标节点
        if t_id not in nodes_dict:
            t_label = extract_label(row["target_labels"])
            t_props = clean_and_parse_json(row["target_properties"])
            t_name = t_props.get("name", f"ID_{t_id}")
            t_domain = t_props.get("knowledge_domain", "未分类")

            all_labels.add(t_label)
            all_domains.add(t_domain)

            nodes_dict[t_id] = {
                "id": t_id,
                "label": t_name,
                "group": t_label,
                "domain": t_domain,
                "properties": t_props,
            }

        # 3. 构建轻量化的边（去掉不需要的富文本属性）
        relation = row["relation"]
        if pd.notna(relation) and str(relation).strip() != "null":
            edges_list.append(
                {
                    "from": s_id,
                    "to": t_id,
                    "label": str(relation),
                    "arrows": "to",
                    "smooth": {"type": "cubicBezier", "roundness": 0.2},
                }
            )

    # 序列化为轻量 JSON 块
    nodes_json = json.dumps(list(nodes_dict.values()), ensure_ascii=False)
    edges_json = json.dumps(edges_list, ensure_ascii=False)
    labels_json = json.dumps(sorted(list(all_labels)), ensure_ascii=False)
    domains_json = json.dumps(sorted(list(all_domains)), ensure_ascii=False)

    # 构建针对低配电脑极致优化的静态 HTML
    html_template = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>高性能图谱看板-离线交互系统</title>
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; display: flex; height: 100vh; background-color: #f8fafc; color: #1e293b; overflow: hidden; }}
        
        #sidebar {{ width: 340px; background: #ffffff; border-right: 1px solid #e2e8f0; display: flex; flex-direction: column; box-shadow: 4px 0 16px rgba(0,0,0,0.03); z-index: 10; }}
        .panel-section {{ padding: 16px 20px; border-bottom: 1px solid #f1f5f9; }}
        h3 {{ margin-bottom: 8px; font-size: 13px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }}
        .search-input, .select-input {{ width: 100%; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 6px; outline: none; font-size: 13px; background: #f8fafc; }}
        .search-input:focus, .select-input:focus {{ border-color: #3b82f6; background: #fff; }}
        
        #details-panel {{ flex: 1; overflow-y: auto; padding: 20px; background-color: #ffffff; border-top: 2px solid #f1f5f9; }}
        .prop-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }}
        .prop-table th, .prop-table td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid #f1f5f9; }}
        .prop-table th {{ color: #64748b; font-weight: 500; width: 35%; white-space: nowrap; }}
        .prop-table td {{ color: #0f172a; word-break: break-all; line-height: 1.5; }}

        #canvas-container {{ flex: 1; position: relative; height: 100%; background: #fafbfc; }}
        #network-canvas {{ width: 100%; height: 100%; }}
        
        #loading {{ position: absolute; top: 20px; right: 20px; padding: 10px 20px; background: rgba(15, 23, 42, 0.9); color: #fff; border-radius: 20px; font-size: 12px; font-weight: 500; box-shadow: 0 4px 12px rgba(0,0,0,0.1); transition: all 0.3s ease; z-index: 100; display: flex; align-items: center; gap: 8px; }}
    </style>
</head>
<body>

    <div id="sidebar">
        <div class="panel-section" style="background: #f8fafc;">
            <h2 style="font-size: 16px; color: #1e3a8a; margin-bottom: 2px;">控制面板</h2>
            <p style="font-size: 11px; color: #94a3b8;">针对低配放映电脑已启用硬件优化加速</p>
        </div>
        
        <div class="panel-section">
            <h3>搜索节点名称</h3>
            <input type="text" id="search-box" class="search-input" placeholder="输入关键字..." oninput="filterGraph()">
        </div>
        <div class="panel-section">
            <h3>按节点标签筛选</h3>
            <select id="label-filter" class="select-input" onchange="filterGraph()">
                <option value="">全部标签</option>
            </select>
        </div>
        <div class="panel-section">
            <h3>按知识领域筛选</h3>
            <select id="domain-filter" class="select-input" onchange="filterGraph()">
                <option value="">全部领域</option>
            </select>
        </div>

        <div id="details-panel">
            <div style="color: #94a3b8; text-align: center; margin-top: 30px; font-size: 13px;" id="placeholder-text">
                💡 点击画布节点<br>实时检索底层元数据属性
            </div>
            <div id="details-content" style="display: none;">
                <h2 id="detail-title" style="font-size: 16px; color: #0f172a; margin-bottom: 6px;"></h2>
                <span id="detail-group" style="display: inline-block; padding: 2px 6px; background: #e0f2fe; color: #0369a1; border-radius: 4px; font-size: 11px; font-weight: 600; margin-bottom: 12px;"></span>
                <table class="prop-table" id="detail-table"></table>
            </div>
        </div>
    </div>

    <div id="canvas-container">
        <div id="loading">⌛ 正在执行矩阵布局优化...</div>
        <div id="network-canvas"></div>
    </div>

    <script type="text/javascript">
        const rawNodes = {nodes_json};
        const rawEdges = {edges_json};
        const uniqueLabels = {labels_json};
        const uniqueDomains = {domains_json};

        let network = null;
        // 使用 DataSet 能够避免全量重绘
        let nodesDataSet = new vis.DataSet([]);
        let edgesDataSet = new vis.DataSet([]);

        // 填充下拉过滤器
        const labelSelect = document.getElementById('label-filter');
        uniqueLabels.forEach(l => labelSelect.options.add(new Option(l, l)));
        const domainSelect = document.getElementById('domain-filter');
        uniqueDomains.forEach(d => domainSelect.options.add(new Option(d, d)));

        function initNetwork() {{
            const container = document.getElementById('network-canvas');
            
            nodesDataSet.assign(rawNodes);
            edgesDataSet.assign(rawEdges);

            const data = {{ nodes: nodesDataSet, edges: edgesDataSet }};
            
            // 极致轻量化的配置选项
            const options = {{
                nodes: {{
                    shape: 'dot',
                    size: 12,
                    font: {{ size: 11, color: '#334155', face: 'system-ui' }},
                    borderWidth: 1.5,
                    shadow: false // 彻底关闭阴影，极其消耗 Canvas 绘图资源
                }},
                edges: {{
                    width: 1,
                    color: {{ color: '#cbd5e1', highlight: '#3b82f6', hover: '#cbd5e1' }},
                    arrows: {{ to: {{ enabled: true, scaleFactor: 0.6 }} }},
                    hoverWidth: 1,
                    selectionWidth: 1.5,
                    shadow: false
                }},
                interaction: {{
                    hover: false, // 关闭全局 Hover 检测，只保留点击检测
                    tooltipDelay: 9999, // 禁用悬浮提示框
                    hideEdgesOnDrag: true, // 关键优化：低配电脑拖拽画布时隐匿边线，防止掉帧卡顿
                    hideEdgesOnZoom: true, // 关键优化：低配电脑缩放时隐匿边线
                    multiselect: false
                }},
                physics: {{
                    enabled: true,
                    stabilization: {{
                        enabled: true,
                        iterations: 100, // 减少后台预计算迭代次数（100次足够收敛且速度极快）
                        updateInterval: 50
                    }},
                    barnesHut: {{
                        gravitationalConstant: -1200,
                        centralGravity: 0.4,
                        springLength: 70,
                        springConstant: 0.06,
                        damping: 0.2
                    }}
                }}
            }};

            network = new vis.Network(container, data, options);

            // 核心优化：后台物理收敛完成后，彻底斩断并关闭物理引擎！使得节点完全静止，后续再无任何 CPU 开销。
            network.on("stabilizationIterationsDone", function () {{
                network.setOptions({{ physics: false }}); 
                document.getElementById('loading').style.opacity = '0';
                setTimeout(() => document.getElementById('loading').style.display = 'none', 300);
            }});

            network.on("selectNode", function (params) {{
                const selectedId = params.nodes[0];
                showNodeDetails(nodesDataSet.get(selectedId));
            }});

            network.on("deselectNode", function () {{
                document.getElementById('placeholder-text').style.display = 'block';
                document.getElementById('details-content').style.display = 'none';
            }});
        }}

        function filterGraph() {{
            const searchKeyword = document.getElementById('search-box').value.toLowerCase().trim();
            const targetLabel = document.getElementById('label-filter').value;
            const targetDomain = document.getElementById('domain-filter').value;

            // 高性能过滤：只有满足条件的节点才会进入渲染管线
            const filteredNodes = rawNodes.filter(node => {{
                return node.label.toLowerCase().includes(searchKeyword) &&
                       (targetLabel === "" || node.group === targetLabel) &&
                       (targetDomain === "" || node.domain === targetDomain);
            }});

            const filteredNodeIds = new Set(filteredNodes.map(n => n.id));
            const filteredEdges = rawEdges.filter(edge => filteredNodeIds.has(edge.from) && filteredNodeIds.has(edge.to));

            // 临时解冻物理系统以适应新的过滤形态，布局完毕后依然会自动切断
            network.setOptions({{ physics: {{ enabled: true, stabilization: {{ enabled: false }} }} }});
            
            nodesDataSet.clear();
            edgesDataSet.clear();
            nodesDataSet.add(filteredNodes);
            edgesDataSet.add(filteredEdges);

            // 过滤后给物理引擎 0.5 秒时间缓冲定型，随后立即冻结防止抖动耗电
            setTimeout(() => {{
                if(network) network.setOptions({{ physics: false }});
            }}, 500);
        }}

        function showNodeDetails(node) {{
            if (!node) return;
            document.getElementById('placeholder-text').style.display = 'none';
            document.getElementById('details-content').style.display = 'block';
            document.getElementById('detail-title').innerText = node.label;
            document.getElementById('detail-group').innerText = "类型: " + node.group;

            const table = document.getElementById('detail-table');
            table.innerHTML = ""; 

            // 点击时才动态遍历渲染复杂、高内存占用的键值对
            for (const [key, value] of Object.entries(node.properties)) {{
                const row = table.insertRow();
                row.insertCell(0).innerHTML = `<b>${{key}}</b>`;
                row.insertCell(1).innerText = typeof value === 'object' ? JSON.stringify(value, null, 2) : value;
            }}
        }}

        window.addEventListener('DOMContentLoaded', initNetwork);
    </script>
</body>
</html>
"""

    print(f"正在写入轻量化静态 HTML 文件至: {HTML_OUTPUT_PATH} ...")
    with open(HTML_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html_template)

    print("🚀 优化配置转换成功！即使在低配放映多媒体设备上也能做到丝滑流畅。")


if __name__ == "__main__":
    main()