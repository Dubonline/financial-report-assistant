from app.agent_factory import build_model
from app.tools import retrieval


def test_company_lookup():
    results = retrieval.search_financial_reports.invoke(
        {"query": "Apple 2014 long-term debt"}
    )
    assert results, "真实 agent.db 中没有找到候选报告"
    print([item["report_id"] for item in results])


def test_model():
    llm = build_model()
    resp = llm.invoke('简要说明财报中经营现金流与净利润的区别，不引用未经检索的公司数值。')
    print(resp)

if __name__ == "__main__":
    # test_company_lookup()
    test_model()