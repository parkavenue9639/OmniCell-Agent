import os
import scanpy as sc
import anndata
anndata.settings.allow_write_nullable_strings = True
import pandas as pd
import json

# 向 Python 路径中插入 src，以便从我们自己的图解契约库中拉取定义
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from omnicell_agent.schema.contract import MarkerTableContract, MarkerGene
from omnicell_agent.core.logger import logger

def prepare_pbmc3k():
    """
    下载标准 PBMC3k 矩阵集，为图引擎前期的单独验证提供基准。
    1. 保存原始裸矩阵 (.h5ad) -> 供 Sandbox Code Programmer 测试用。
    2. 生成聚类及 Marker 差异表 (JSON) -> 供 Sub-Graph B Annotator 测试用。
    """
    data_dir = os.path.join(os.path.dirname(__file__), "../data")
    os.makedirs(data_dir, exist_ok=True)
    
    raw_h5ad_path = os.path.join(data_dir, "pbmc3k_raw.h5ad")
    contract_json_path = os.path.join(data_dir, "pbmc3k_markers.json")

    logger.info(">>> 1. 正在获取 Scanpy 官方 3k PBMC 数据集...")
    # SCANPY_DIR 下默认缓存
    adata = sc.datasets.pbmc3k()
    
    # 存储原始未处理的 h5ad 作为 Sub-A 的输入模拟
    logger.info(f">>> 2. 落盘用于测试图 A 的原始测序字典: {raw_h5ad_path}")
    adata.write(raw_h5ad_path)
    
    logger.info(">>> 3. 在本地启动标准数据管线计算 Marker Genes 从而生成契约标准产物...")
    sc.pp.filter_cells(adata, min_genes=200)
    sc.pp.filter_genes(adata, min_cells=3)
    adata.var['mt'] = adata.var_names.str.startswith('MT-').astype(bool)
    sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)
    adata = adata[adata.obs.pct_counts_mt < 5, :]
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, min_mean=0.0125, max_mean=3, min_disp=0.5)
    adata = adata[:, adata.var.highly_variable]
    sc.pp.scale(adata, max_value=10)
    sc.tl.pca(adata, svd_solver='arpack')
    sc.pp.neighbors(adata, n_neighbors=10, n_pcs=40)
    sc.tl.leiden(adata, resolution=0.5) # 标准 Leiden 聚类
    
    # 获取特异表达基因
    # 开启 pts=True 会带上表达率（本簇细胞表达比例及其它簇表达比例），这对后续验证是致命核心数据。
    sc.tl.rank_genes_groups(adata, 'leiden', method='wilcoxon', pts=True)
    
    # 【重点】计算由契约严厉规定的表达比例指标 (pct_1, pct_2) 
    # 过滤掉变化倍数过低的基因
    sc.tl.filter_rank_genes_groups(adata, min_fold_change=1)
    
    logger.info(">>> 4. 正在抽取提取统计 DataFrame，编排进入 Data Contract 桥梁契约实例...")
    clusters = adata.obs['leiden'].cat.categories
    contract_markers = []
    
    # 抽取基因阳性比例组字典转换为 DataFrame 便于极速检索
    pts = pd.DataFrame(adata.uns['rank_genes_groups']['pts'])
    pts_rest = pd.DataFrame(adata.uns['rank_genes_groups']['pts_rest'])
    
    for cluster_id in clusters:
        # 取每个簇 top 50，dropna 防范 filter 引发的空洞
        df = sc.get.rank_genes_groups_df(adata, group=cluster_id).dropna(subset=['names']).head(50)
        for _, row in df.iterrows():
            gene = str(row['names'])
            p_val = float(row['pvals']) if not pd.isna(row['pvals']) else 1.0
            p_val_adj = float(row['pvals_adj']) if not pd.isna(row['pvals_adj']) else 1.0
            log2FC = float(row['logfoldchanges']) if not pd.isna(row['logfoldchanges']) else 0.0
            
            # 使用真实的表达式比例数据抽取
            pct_1 = float(pts.loc[gene, cluster_id]) if gene in pts.index else 0.0
            pct_2 = float(pts_rest.loc[gene, cluster_id]) if gene in pts_rest.index else 0.0
            
            marker = MarkerGene(
                gene_name=gene,
                cluster_id=str(cluster_id),
                p_val=p_val,
                p_val_adj=p_val_adj,
                log2FC=log2FC,
                pct_1=pct_1, 
                pct_2=pct_2 
            )
            contract_markers.append(marker)
            
    # 实例化 Contract 并自动执行安全序列化落地
    table_contract = MarkerTableContract(
        metadata={"source": "scanpy.datasets.pbmc3k", "resolution": 0.5, "mocked_pct": True},
        markers=contract_markers
    )
    table_contract.save_to_json(contract_json_path)
    logger.info(f">>> 5. 成功输出受控测试契约文件至: {contract_json_path}")
    logger.info("🎉 前置阶段准备完毕！这批数据随时可进入 Sub-Graph AB 进行全链断点隔离测试。")

if __name__ == "__main__":
    prepare_pbmc3k()
