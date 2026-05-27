import pandas as pd
import re
import os

def classify_label(node_name):
    """根据航发原理术语特征自动划分类别标签"""
    name_lower = str(node_name).lower().strip()
    
    # 1. 特征截面
    if 'station' in name_lower or re.search(r'\b(s\d|station\s*\d)\b', name_lower):
        return 'Station'
    
    # 2. 理论、循环、方程与分析方法
    if any(k in name_lower for k in ['cycle', 'analysis', 'theory', 'relation', 'equation', 'balance', 'logic', 'consistency', 'law']):
        return 'Equation'
        
    # 3. 运行极限、工作天线、不完美损失现象、气动失稳边界
    if any(k in name_lower for k in ['limit', 'operation', 'surge', 'stall', 'irreversibilit', 'choked', 'imbalance', 'drop', 'loss', 'day', 'boundary']):
        return 'BoundaryCondition'
        
    # 4. 物理实体与推进系统部件
    if any(k in name_lower for k in ['system', 'engine', 'compressor', 'combustor', 'burner', 'turbine', 'nozzle', 'inlet', 'vane', 'afterburner', 'pump', 'propulsion']):
        return 'Component'
        
    # 5. 气动热力学参数与物理量（默认兜底）
    return 'Parameter'

def standardize_relationship(rel_str):
    """规范化关系命名格式，转为大写下划线"""
    rel = str(rel_str).strip().lower()
    rel = rel.replace(' ', '_').replace('-', '_')
    if rel == 'classified_into': rel = 'CLASSIFIED_TO'
    if rel == 'determines_in_off_design': rel = 'DETERMINES_OFF_DESIGN'
    return rel.upper()

def process_engine_kg_csv(target_dir):
    refer_path = os.path.join(target_dir, 'refer.csv')
    cross_path = os.path.join(target_dir, 'cross.csv')

    # ---- 1. 处理基础三元组 refer.csv ----
    print("----------------------------------------")
    if os.path.exists(refer_path):
        try:
            # 尝试用 utf-8-sig (自动去BOM) 或 gbk 兼容中文读取
            try:
                df_refer = pd.read_csv(refer_path, encoding='utf-8-sig')
            except UnicodeDecodeError:
                df_refer = pd.read_csv(refer_path, encoding='gbk')
                
            print(f"成功读取 refer.csv，包含 {len(df_refer)} 条原始数据。")
            
            # 清理列名的空格
            df_refer.columns = [c.strip() for c in df_refer.columns]
            
            # 动态寻找第六列（核心释义列）
            desc_col = df_refer.columns[5] if len(df_refer.columns) >= 6 else df_refer.columns[-1]
            
            df_refer_new = pd.DataFrame()
            df_refer_new['源节点名称'] = df_refer['主语'].astype(str).str.strip()
            df_refer_new['源节点标签'] = df_refer['主语'].apply(classify_label)
            df_refer_new['关系类型'] = df_refer['关系'].apply(standardize_relationship)
            df_refer_new['目标节点名称'] = df_refer['宾语'].astype(str).str.strip()
            df_refer_new['目标节点标签'] = df_refer['宾语'].apply(classify_label)
            df_refer_new['知识分类'] = df_refer['知识分类'].astype(str).str.strip()
            df_refer_new['页码'] = df_refer['页码'].astype(str).str.strip()
            df_refer_new['核心释义'] = df_refer[desc_col].astype(str).str.strip()
            
            out_refer = os.path.join(target_dir, 'refer_restructured.csv')
            df_refer_new.to_csv(out_refer, index=False, encoding='utf-8-sig')
            print(f"🎉 成功生成重构文件: {out_refer}")
            
        except Exception as e:
            print(f"❌ 处理 refer.csv 时发生内部错误: {e}")
            print("请检查 refer.csv 的列名是否严格为：主语, 关系, 宾语, 知识分类, 页码, 原文参考/核心概念释义")
    else:
        print(f"❌ 未在指定目录下找到 refer.csv 文件，请检查路径。")

    # ---- 2. 处理跨章节高阶逻辑 cross.csv ----
    print("----------------------------------------")
    if os.path.exists(cross_path):
        try:
            try:
                df_cross = pd.read_csv(cross_path, encoding='utf-8-sig')
            except UnicodeDecodeError:
                df_cross = pd.read_csv(cross_path, encoding='gbk')
                
            print(f"成功读取 cross.csv，包含 {len(df_cross)} 条原始数据。")
            df_cross.columns = [c.strip() for c in df_cross.columns]
            
            desc_col_cross = df_cross.columns[5] if len(df_cross.columns) >= 6 else df_cross.columns[-1]
            
            df_cross_new = pd.DataFrame()
            df_cross_new['源节点'] = df_cross['主语'].astype(str).str.strip()
            df_cross_new['源标签'] = df_cross['主语'].apply(classify_label)
            df_cross_new['关系类型'] = df_cross['关系'].apply(standardize_relationship)
            
            def extract_trend(rel):
                rel_lower = str(rel).lower()
                if 'increase' in rel_lower: return '正相关'
                if 'decrease' in rel_lower or 'drop' in rel_lower: return '负相关'
                if 'determine' in rel_lower: return '刚性锁定'
                return '热力学耦合'
                
            df_cross_new['影响趋势'] = df_cross['关系'].apply(extract_trend)
            df_cross_new['目标节点'] = df_cross['宾语'].astype(str).str.strip()
            df_cross_new['目标标签'] = df_cross['宾语'].apply(classify_label)
            df_cross_new['跨章节逻辑说明'] = df_cross['跨章节逻辑说明'].astype(str).str.strip()
            df_cross_new['核心释义'] = df_cross[desc_col_cross].astype(str).str.strip()
            
            out_cross = os.path.join(target_dir, 'cross_restructured.csv')
            df_cross_new.to_csv(out_cross, index=False, encoding='utf-8-sig')
            print(f"🎉 成功生成重构文件: {out_cross}")
            
        except Exception as e:
            print(f"❌ 处理 cross.csv 时发生内部错误: {e}")
    else:
        print(f"❌ 未在指定目录下找到 cross.csv 文件。")
    print("----------------------------------------")

if __name__ == '__main__':
    # 填入你电脑上存放这两个 CSV 文件的【真实绝对路径】
    # 记得在最前方保留 r 字母
    WORKING_DIRECTORY = r"e:\coding\python\26-5-27principle of areoengines" 
    
    process_engine_kg_csv(WORKING_DIRECTORY)