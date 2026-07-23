import json
from pathlib import Path
from typing import IO, Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class MarkerGene(BaseModel):
    """
    单个基因的差异表达指标抽象。
    允许 Graph A 输出附加的科学指标，同时规范 Graph B 依赖的稳定字段。
    """
    model_config = ConfigDict(extra="allow")

    gene_name: str = Field(
        validation_alias=AliasChoices("gene_name", "names", "gene")
    )
    cluster_id: str = Field(
        validation_alias=AliasChoices("cluster_id", "cluster")
    )
    p_val: float = Field(
        validation_alias=AliasChoices("p_val", "pvals", "pval"),
        description="原始 P 值",
    )
    p_val_adj: float = Field(
        validation_alias=AliasChoices("p_val_adj", "pvals_adj", "pval_adj"),
        description="BH 校正后的 P 值",
    )
    log2FC: float = Field(
        validation_alias=AliasChoices("log2FC", "logfoldchanges", "avg_log2FC"),
        description="Fold change",
    )
    pct_1: float = Field(
        validation_alias=AliasChoices("pct.1", "pct_1"),
        description="本细胞簇表达比例",
    )
    pct_2: float = Field(
        validation_alias=AliasChoices("pct.2", "pct_2"),
        description="其他细胞簇表达比例",
    )
    score: float | None = None
    is_surface_protein: bool | None = None


class MarkerTableContract(BaseModel):
    """
    Graph A 输出与 Graph B 输入之间的正式 marker-table artifact 契约。
    """
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="来源数据版本、计算引擎版本、降维聚类超参等附带上下文",
    )
    markers: list[MarkerGene]

    def save_to_json(self, path: str | Path) -> None:
        """将校验通过的契约写入 conversation workspace。"""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))

    @classmethod
    def load_from_stream(cls, stream: IO[bytes] | IO[str]) -> "MarkerTableContract":
        """从调用方已经固定并校验的流读取 marker-table 契约。"""

        data = json.load(stream)
        # Graph A 的标准导出目标是 marker JSON array；完整 envelope 用于
        # 携带可选 provenance metadata。两者都是当前正式输入形态。
        if isinstance(data, list):
            data = {"metadata": {}, "markers": data}

        return cls(**data)

    @classmethod
    def load_from_json(cls, path: str | Path) -> "MarkerTableContract":
        """读取 Graph A 的 marker array 或完整 marker-table envelope。"""

        with open(path, "r", encoding="utf-8") as stream:
            return cls.load_from_stream(stream)
