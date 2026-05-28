import pandas as pd
from pyvis.network import Network

# ==============================
# 读取数据
# ==============================

nodes = pd.read_csv("nodes.csv")

relations = pd.read_csv("relations.csv")

print("Nodes:", len(nodes))
print("Relations:", len(relations))

# ==============================
# 创建网络
# ==============================

net = Network(

    height="900px",

    width="100%",

    bgcolor="#111111",

    font_color="white",

    directed=True

)

# ==============================
# 节点颜色
# ==============================

color_map = {

    "Component": "#4F81BD",

    "Parameter": "#9BBB59",

    "Equation": "#F79646",

    "BoundaryCondition": "#C0504D"

}

# ==============================
# 添加节点
# ==============================

for _, row in nodes.iterrows():

    node_id = str(row["id"])

    node_name = str(row["name"])

    category = str(row["category"])

    subsystem = str(row.get("subsystem", ""))

    color = color_map.get(

        category,

        "#CCCCCC"

    )

    net.add_node(

        node_id,

        label=node_name,

        title=f"""

        <b>{node_name}</b><br>

        Category: {category}<br>

        Subsystem: {subsystem}

        """,

        color=color

    )

# ==============================
# 允许的关系
# ==============================

allowed_relations = [

    "AFFECTS",

    "USED_IN",

    "LIMITED_BY"

]

# ==============================
# 添加边
# ==============================

for _, row in relations.iterrows():

    relation = str(row["relation"])

    if relation not in allowed_relations:

        continue

    source = str(row["source"])

    target = str(row["target"])

    net.add_edge(

        source,

        target,

        title=relation,

        label=relation,

        arrows="to"

    )

# ==============================
# 物理布局
# ==============================

net.barnes_hut(

    gravity=-5000,

    central_gravity=0.2,

    spring_length=180,

    spring_strength=0.03,

    damping=0.95

)

# ==============================
# 保存 HTML
# ==============================

net.save_graph(

    "engine_knowledge_graph.html"

)

print("\n完成")
print("输出文件:")
print("engine_knowledge_graph.html")