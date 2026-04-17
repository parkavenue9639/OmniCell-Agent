import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, AliasChoices
from typing import List, Dict, Any, Optional

class MarkerGene(BaseModel):
    """
    单个基因的差异表达指标抽象。
    利用 ConfigDict(extra="allow") 保证了横向扩展能力：后续即使单细胞分析新增了 
    诸如 'tf_score'、'go_enrichment' 等未经预先定义的列，反序列化也不会崩溃，而是包容性挂载。
    """
    model_config = ConfigDict(extra="allow")

    gene_name: str = Field(..., validation_alias=AliasChoices("gene_name", "names", "gene"))
    cluster_id: str = Field(..., validation_alias=AliasChoices("cluster_id", "cluster"))
    p_val: float = Field(..., validation_alias=AliasChoices("p_val", "pvals", "pval"), description="原始 P 值")
    p_val_adj: float = Field(..., validation_alias=AliasChoices("p_val_adj", "pvals_adj", "pval_adj"), description="BH校正后的 P 值")
    log2FC: float = Field(..., validation_alias=AliasChoices("log2FC", "logfoldchanges", "avg_log2FC"), description="Fold change")
    
    # 支持在验证时同时接受 "pct_1" 或是带有句点的 "pct.1" (Scanpy/Seurat 格式)
    pct_1: float = Field(..., validation_alias=AliasChoices("pct.1", "pct_1"), description="本细胞簇表达比例")
    pct_2: float = Field(..., validation_alias=AliasChoices("pct.2", "pct_2"), description="其他细胞簇表达比例")
    
    # 预留槽位：日后可激活的可选补充列
    score: Optional[float] = None
    is_surface_protein: Optional[bool] = None


class MarkerTableContract(BaseModel):
    """
    全量细胞簇差异基因数据统计表的系统级契约载体。
    在 SubA -> SubB 的过渡期充当安全阀。
    """
    metadata: Dict[str, Any] = Field(
        default_factory=dict, 
        description="来源数据版本、计算引擎版本、使用的降维聚类超参等附带上下文"
    )
    markers: List[MarkerGene]
    
    def save_to_json(self, path: str | Path):
        """将校验通过的契约格式落地到安全沙盒共享卷中，供图 B 读取"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))
            
    @classmethod
    def load_from_json(cls, path: str | Path) -> "MarkerTableContract":
        """从文件中拉升并强校验恢复契约对象"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # 兼容沙盘大模型有几率只丢过来一个 JSON Array 的情况
        if isinstance(data, list):
            data = {"metadata": {}, "markers": data}
            
        return cls(**data)
