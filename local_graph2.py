import pandas as pd
import json

# ==========================================
# 读取 CSV
# ==========================================

nodes_df = pd.read_csv("nodes.csv")
relations_df = pd.read_csv("relations.csv")

# ==========================================
# 节点颜色
# ==========================================

color_map = {

    "Component": "#4F81BD",

    "Parameter": "#9BBB59",

    "Equation": "#F79646",

    "BoundaryCondition": "#C0504D"

}

# ==========================================
# 构建节点
# ==========================================

nodes = []

for _, row in nodes_df.iterrows():

    category = str(row["category"])

    color = color_map.get(
        category,
        "#CCCCCC"
    )

    nodes.append({

        "id": str(row["id"]),

        "label": str(row["name"]),

        "group": category,

        "title":
            f"""
            <b>{row['name']}</b><br>
            Category: {category}<br>
            Subsystem: {row.get('subsystem','')}
            """,

        "color": color

    })

# ==========================================
# 构建边
# ==========================================

edges = []

for _, row in relations_df.iterrows():

    edges.append({

        "from": str(row["source"]),

        "to": str(row["target"]),

        "label": str(row["relation"]),

        "title": str(row["relation"]),

        "arrows": "to"

    })

# ==========================================
# HTML
# ==========================================

html = f"""

<!DOCTYPE html>

<html>

<head>

<meta charset="utf-8">

<title>Engine Knowledge Graph</title>

<script
type="text/javascript"
src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js">
</script>

<style>

body {{

    margin:0;
    background:#111111;
    color:white;
    font-family:Arial;

}}

#mynetwork {{

    width:100vw;
    height:90vh;
    border:1px solid #333;

}}

#controls {{

    padding:10px;
    background:#222;

}}

input, select {{

    padding:6px;
    margin-right:10px;

}}

button {{

    padding:6px 12px;

}}

</style>

</head>

<body>

<div id="controls">

<input
type="text"
id="searchBox"
placeholder="Search node..."
>

<select id="groupFilter">

<option value="ALL">ALL</option>

<option value="Component">Component</option>

<option value="Parameter">Parameter</option>

<option value="Equation">Equation</option>

<option value="BoundaryCondition">BoundaryCondition</option>

</select>

<button onclick="searchNode()">
Search
</button>

<button onclick="filterGroup()">
Filter
</button>

<button onclick="resetGraph()">
Reset
</button>

</div>

<div id="mynetwork"></div>

<script>

var nodes = new vis.DataSet(
{json.dumps(nodes)}
);

var edges = new vis.DataSet(
{json.dumps(edges)}
);

var container =
document.getElementById('mynetwork');

var data = {{

    nodes: nodes,
    edges: edges

}};

var options = {{

    physics: {{

        stabilization: false,

        barnesHut: {{

            gravitationalConstant: -3000,
            springLength: 180

        }}

    }},

    nodes: {{

        shape: 'dot',
        size: 18,

        font: {{

            color: 'white',
            size: 14

        }}

    }},

    edges: {{

        color: '#888888',

        arrows: {{

            to: true

        }},

        font: {{

            color: '#cccccc',
            size: 10,
            align: 'middle'

        }}

    }},

    interaction: {{

        hover: true,

        navigationButtons: true,

        keyboard: true

    }}

}};

var network =
new vis.Network(
container,
data,
options
);

function searchNode() {{

    var keyword =
    document.getElementById(
    "searchBox"
    ).value.toLowerCase();

    var found = null;

    nodes.forEach(function(node) {{

        if(node.label.toLowerCase()
        .includes(keyword)) {{

            found = node.id;

        }}

    }});

    if(found) {{

        network.focus(found, {{

            scale:1.5,

            animation:true

        }});

        network.selectNodes([found]);

    }}

}}

function filterGroup() {{

    var group =
    document.getElementById(
    "groupFilter"
    ).value;

    if(group === "ALL") {{

        nodes.forEach(function(node) {{

            nodes.update({{

                id: node.id,
                hidden:false

            }});

        }});

        return;

    }}

    nodes.forEach(function(node) {{

        if(node.group === group) {{

            nodes.update({{

                id: node.id,
                hidden:false

            }});

        }}

        else {{

            nodes.update({{

                id: node.id,
                hidden:true

            }});

        }}

    }});

}}

function resetGraph() {{

    nodes.forEach(function(node) {{

        nodes.update({{

            id: node.id,
            hidden:false

        }});

    }});

    network.fit();

}}

</script>

</body>

</html>

"""

# ==========================================
# 输出 HTML
# ==========================================

with open(

    "interactive_engine_kg.html",

    "w",

    encoding="utf-8"

) as f:

    f.write(html)

print("完成")
print("输出:")
print("interactive_engine_kg.html")