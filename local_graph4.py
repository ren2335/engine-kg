import json
import re
import pandas as pd

# 定义输入的 CSV 文件和输出的 HTML 文件路径
CSV_FILE_PATH = "neo4j_query_table_data_2026-5-28.csv"
HTML_OUTPUT_PATH = "graph_visualization.html"


def clean_and_parse_json(json_str):
    """安全解析 Neo4j 导出属性中的复杂/不规范 JSON 字符串"""
    if pd.isna(json_str) or not str(json_str).strip():
        return {}
    try:
        # 尝试直接解析
        return json.loads(json_str)
    except json.JSONDecodeError:
        # 如果解析失败，处理一些非标准简写（例如未加双引号的键或换行符）
        try:
            # 补全可能缺失的规范化（这里针对常见 Aura 导出做基本兼容）
            fixed_str = re.sub(r"(\b\w+\b)(?=\s*:)", r'"\1"', json_str)
            return json.loads(fixed_str)
        except Exception:
            return {"raw": str(json_str)}


def extract_label(label_str):
    """提取标签，例如将 '[Component]' 转换为 'Component'"""
    if pd.isna(label_str):
        return "Unknown"
    return str(label_str).strip("[] ")


def main():
    # 1. 读取 CSV 数据
    print(f"正在读取 CSV 文件: {CSV_FILE_PATH} ...")
    df = pd.read_csv(CSV_FILE_PATH)

    nodes_dict = {}
    edges_list = []

    # 记录用于筛选的元数据集合
    all_labels = set()
    all_domains = set()

    # 2. 解析拓扑结构
    print("正在解析节点与拓扑关系...")
    for _, row in df.iterrows():
        # 解析源节点
        s_id = str(row["source_id"])
        s_label = extract_label(row["source_labels"])
        s_props = clean_and_parse_json(row["source_properties"])

        # 解析目标节点
        t_id = str(row["target_id"])
        t_label = extract_label(row["target_labels"])
        t_props = clean_and_parse_json(row["target_properties"])

        # 提取核心属性
        s_name = s_props.get("name", f"ID_{s_id}")
        s_domain = s_props.get("knowledge_domain", "未分类")
        t_name = t_props.get("name", f"ID_{t_id}")
        t_domain = t_props.get("knowledge_domain", "未分类")

        all_labels.update([s_label, t_label])
        all_domains.update([s_domain, t_domain])

        # 将源节点加入字典
        if s_id not in nodes_dict:
            nodes_dict[s_id] = {
                "id": s_id,
                "label": s_name,
                "group": s_label,
                "domain": s_domain,
                "title": f"<b>{s_name}</b> ({s_label})",
                "properties": s_props,
            }

        # 将目标节点加入字典
        if t_id not in nodes_dict:
            nodes_dict[t_id] = {
                "id": t_id,
                "label": t_name,
                "group": t_label,
                "domain": t_domain,
                "title": f"<b>{t_name}</b> ({t_label})",
                "properties": t_props,
            }

        # 解析边关系 (若关系为空则跳过)
        relation = row["relation"]
        if pd.notna(relation) and str(relation).strip() != "null":
            edges_list.append(
                {
                    "from": s_id,
                    "to": t_id,
                    "label": str(relation),
                    "arrows": "to",
                    "font": {"align": "top", "size": 10},
                }
            )

    nodes_json = json.dumps(list(nodes_dict.values()), ensure_ascii=False)
    edges_json = json.dumps(edges_list, ensure_ascii=False)
    labels_json = json.dumps(sorted(list(all_labels)), ensure_ascii=False)
    domains_json = json.dumps(sorted(list(all_domains)), ensure_ascii=False)

    # 3. 构建静态 HTML 模板
    html_template = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>知识图谱离线可视化交互系统</title>
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; display: flex; height: 100vh; background-color: #f4f6f9; color: #333; }}
        
        /* 左侧控制台 */
        #sidebar {{ width: 320px; background-color: #ffffff; border-right: 1px solid #e0e4ec; display: flex; flex-direction: column; box-shadow: 2px 0 8px rgba(0,0,0,0.05); z-index: 10; }}
        .panel-section {{ padding: 20px; border-bottom: 1px solid #e0e4ec; }}
        h3 {{ margin-bottom: 12px; font-size: 15px; color: #1e293b; letter-spacing: 0.5px; }}
        .search-input, .select-input {{ width: 100%; padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 6px; outline: none; font-size: 14px; transition: border 0.2s; }}
        .search-input:focus, .select-input:focus {{ border-color: #3b82f6; }}
        
        /* 属性详情页 */
        #details-panel {{ flex: 1; overflow-y: auto; padding: 20px; background-color: #f8fafc; }}
        .prop-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }}
        .prop-table th, .prop-table td {{ text-align: left; padding: 8px; border-bottom: 1px solid #e2e8f0; }}
        .prop-table th {{ color: #64748b; font-weight: 500; width: 35%; }}
        .prop-table td {{ color: #0f172a; word-break: break-all; }}

        /* 右侧画布区域 */
        #canvas-container {{ flex: 1; position: relative; height: 100%; }}
        #network-canvas {{ width: 100%; height: 100%; background-color: #fafbfc; }}
        
        /* 统计加载提示 */
        #loading {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); padding: 15px 30px; background: rgba(0,0,0,0.75); color: #fff; border-radius: 8px; font-size: 14px; pointer-events: none; transition: opacity 0.3s; z-index: 100; }}
    </style>
</head>
<body>

    <div id="sidebar">
        <div class="panel-section">
            <h2 style="font-size: 18px; color: #2563eb; margin-bottom: 4px;">知识图谱数据看板</h2>
            <p style="font-size: 12px; color: #64748b;">数据源: Neo4j Aura 离线导出</p>
        </div>
        
        <div class="panel-section">
            <h3>搜索节点名称</h3>
            <input type="text" id="search-box" class="search-input" placeholder="输入关键字实时检索..." oninput="filterGraph()">
        </div>
        <div class="panel-section">
            <h3>按节点标签 (Label) 过滤</h3>
            <select id="label-filter" class="select-input" onchange="filterGraph()">
                <option value="">全部标签</option>
            </select>
        </div>
        <div class="panel-section">
            <h3>按知识领域 (Domain) 过滤</h3>
            <select id="domain-filter" class="select-input" onchange="filterGraph()">
                <option value="">全部领域</option>
            </select>
        </div>

        <div id="details-panel">
            <div style="color: #94a3b8; text-align: center; margin-top: 40px;" id="placeholder-text">
                点击画布中的节点<br>查看底层完整的属性架构
            </div>
            <div id="details-content" style="display: none;">
                <h2 id="detail-title" style="font-size: 18px; color: #1e3a8a; margin-bottom: 5px;"></h2>
                <span id="detail-group" style="display: inline-block; padding: 2px 8px; background: #dbeafe; color: #1e40af; border-radius: 4px; font-size: 11px; font-weight: bold; margin-bottom: 15px;"></span>
                <table class="prop-table" id="detail-table"></table>
            </div>
        </div>
    </div>

    <div id="canvas-container">
        <div id="loading">正在进行拓扑布局优化，请稍候...</div>
        <div id="network-canvas"></div>
    </div>

    <script type="text/javascript">
        // 原始集成的节点与边数据
        const rawNodes = {nodes_json};
        const rawEdges = {edges_json};
        const uniqueLabels = {labels_json};
        const uniqueDomains = {domains_json};

        let network = null;
        let nodesDataSet = new vis.DataSet([]);
        let edgesDataSet = new vis.DataSet([]);

        // 初始化筛选器组件
        const labelSelect = document.getElementById('label-filter');
        uniqueLabels.forEach(l => {{
            labelSelect.options.add(new Option(l, l));
        }});
        const domainSelect = document.getElementById('domain-filter');
        uniqueDomains.forEach(d => {{
            domainSelect.options.add(new Option(d, d));
        }});

        // 核心渲染方法
        function initNetwork() {{
            const container = document.getElementById('network-canvas');
            
            // 预先载入全量数据
            nodesDataSet.assign(rawNodes);
            edgesDataSet.assign(rawEdges);

            const data = {{ nodes: nodesDataSet, edges: edgesDataSet }};
            
            // 配置高度优化的 Vis-Network 参数（大幅降低静态渲染压力）
            const options = {{
                nodes: {{
                    shape: 'dot',
                    size: 16,
                    font: {{ size: 12, color: '#334155' }},
                    borderWidth: 2,
                    shadow: true
                }},
                edges: {{
                    width: 1,
                    color: {{ color: '#cbd5e1', highlight: '#3b82f6', hover: '#94a3b8' }},
                    smooth: {{ type: 'continuous' }} // 开启连续平滑曲线，大幅缩短大规模物理引擎收敛时间
                }},
                groups: {{
                    Component: {{ color: {{ background: '#60a5fa', border: '#2563eb' }} }},
                    Parameter: {{ color: {{ background: '#34d399', border: '#059669' }} }},
                    Equation: {{ color: {{ background: '#fb7185', border: '#e11d48' }} }},
                    BoundaryCondition: {{ color: {{ background: '#fbbf24', border: '#d97706' }} }}
                }},
                interaction: {{
                    hover: true,
                    tooltipDelay: 200,
                    selectable: true
                }},
                physics: {{
                    stabilization: {{
                        enabled: true,
                        iterations: 150, // 首次预迭代150次，防止页面加载时元素乱飞
                        updateInterval: 25
                    }},
                    barnesHut: {{
                        gravitationalConstant: -2000,
                        centralGravity: 0.3,
                        springLength: 95,
                        springConstant: 0.04
                    }}
                }}
            }};

            network = new vis.Network(container, data, options);

            // 物理引擎稳定后隐藏加载状态提示
            network.on("stabilizationIterationsDone", function () {{
                document.getElementById('loading').style.opacity = '0';
            }});

            // 监听节点选择事件以渲染属性侧边栏
            network.on("selectNode", function (params) {{
                const selectedId = params.nodes[0];
                const nodeData = nodesDataSet.get(selectedId);
                showNodeDetails(nodeData);
            }});

            network.on("deselectNode", function () {{
                document.getElementById('placeholder-text').style.display = 'block';
                document.getElementById('details-content').style.display = 'none';
            }});
        }}

        // 统一级联筛选控制逻辑
        function filterGraph() {{
            const searchKeyword = document.getElementById('search-box').value.toLowerCase().strip ? document.getElementById('search-box').value.toLowerCase().trim() : document.getElementById('search-box').value.toLowerCase();
            const targetLabel = document.getElementById('label-filter').value;
            const targetDomain = document.getElementById('domain-filter').value;

            // 基于条件遍历全量节点过滤
            const filteredNodes = rawNodes.filter(node => {{
                const matchKeyword = node.label.toLowerCase().includes(searchKeyword);
                const matchLabel = targetLabel === "" || node.group === targetLabel;
                const matchDomain = targetDomain === "" || node.domain === targetDomain;
                return matchKeyword && matchLabel && matchDomain;
            }});

            const filteredNodeIds = new Set(filteredNodes.map(n => n.id));

            // 动态保留与之相连的边线，防止产生孤立的无效悬空连线，降低渲染负担
            const filteredEdges = rawEdges.filter(edge => {{
                return filteredNodeIds.has(edge.from) && filteredNodeIds.has(edge.to);
            }});

            // 刷新图画布
            nodesDataSet.clear();
            edgesDataSet.clear();
            nodesDataSet.add(filteredNodes);
            edgesDataSet.add(filteredEdges);
        }}

        // 动态展示底层 KV 属性结构
        function showNodeDetails(node) {{
            document.getElementById('placeholder-text').style.display = 'none';
            document.getElementById('details-content').style.display = 'block';
            
            document.getElementById('detail-title').innerText = node.label;
            document.getElementById('detail-group').innerText = "类型: " + node.group;

            const table = document.getElementById('detail-table');
            table.innerHTML = ""; // 清空旧数据

            // 迭代显示嵌套的复杂 JSON 属性
            for (const [key, value] of Object.entries(node.properties)) {{
                const row = table.insertRow();
                const cellKey = row.insertCell(0);
                const cellVal = row.insertCell(1);
                cellKey.innerHTML = `<b>${{key}}</b>`;
                cellVal.innerText = typeof value === 'object' ? JSON.stringify(value) : value;
            }}
        }}

        // 脚本执行入口
        window.addEventListener('DOMContentLoaded', initNetwork);
    </script>
</body>
</html>
"""

    # 4. 写出静态文件
    print(f"正在写入静态 HTML 文件至: {HTML_OUTPUT_PATH} ...")
    with open(HTML_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html_template)

    print("转换成功！你可以双击直接在任何浏览器中打开此静态文件查阅。")


if __name__ == "__main__":
    main()