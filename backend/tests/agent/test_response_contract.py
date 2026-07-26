from omnicell_agent.agent.response_contract import render_response_contract


def test_response_contract_prioritizes_current_explicit_constraints() -> None:
    contract = render_response_contract()

    assert "路由先于表达" in contract
    assert "必须先调用 load_skill" in contract
    assert "答复很短" in contract
    assert "不要读取数据或执行领域 Tool" in contract
    assert "安全边界、科学真实性、Tool/Artifact 权威契约" in contract
    assert "当前用户" in contract
    assert all(
        term in contract
        for term in ("语言", "篇幅或句数", "受众", "格式", "重点", "排除项")
    )
    assert "历史轮次中的表达偏好" in contract
    assert contract.index("安全边界") < contract.index("当前用户")


def test_response_contract_uses_adaptive_minimum_sufficient_depth() -> None:
    contract = render_response_contract()

    assert "最小充分表达" in contract
    assert "只询问一个概念或原因" in contract
    assert "默认用 2 至 4 句话和一个紧凑段落" in contract
    assert "不使用标题、列表或表格" in contract
    assert "详细说明、教程、报告" in contract
    assert "执行类回复先给结果" in contract
    assert "只问当前继续工作所需的最小问题" in contract


def test_response_contract_preserves_scientific_evidence_boundaries() -> None:
    contract = render_response_contract()

    assert all(
        term in contract
        for term in ("通用知识", "直接观测", "推断", "建议", "不确定性")
    )
    assert "不得把通用经验写成当前数据结论" in contract
    assert "不得杜撰结果" in contract
    assert all(
        term in contract
        for term in ("算法实现", "数据处理流程", "统计假设", "金标准")
    )
    assert all(
        term in contract
        for term in ("相关性", "差异表达", "因果驱动", "充分验证")
    )
    assert all(
        term in contract
        for term in ("算法实际输入", "中间表示", "可视化输出", "因果设计")
    )
    assert all(term in contract for term in ("驱动", "导致", "决定", "证明"))


def test_response_contract_limits_structure_analogy_and_internal_detail() -> None:
    contract = render_response_contract()

    assert "简短答案不要自动添加多级标题、表格或多个例子" in contract
    assert all(
        term in contract
        for term in ("猎奇", "血腥", "贬损", "戏剧化", "错误印象")
    )
    assert "不输出隐藏推理" in contract
    assert "不得通过机械截断" in contract
    assert "输出前检查" in contract
