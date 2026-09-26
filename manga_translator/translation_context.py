"""Build translation history text for a page or page batch."""


def build_previous_page_context(
    context_size,
    all_page_translations,
    original_page_texts,
    has_original_page_texts,
    use_original_text=False,
    current_page_index=None,
    batch_index=None,
    batch_original_texts=None,
):
    """
    跳过句子数为0的页面，取最近 context_size 个非空页面，拼成：
    <|1|>句子
    <|2|>句子
    ...
    的格式；如果没有任何非空页面，返回空串。

    Args:
        use_original_text: 是否使用原文而不是译文作为上下文
        current_page_index: 当前页面索引，用于确定上下文范围
        batch_index: 当前页面在批次中的索引
        batch_original_texts: 当前批次的原文数据
    """
    if context_size <= 0:
        return ""

    # 在并发模式下，需要特殊处理上下文范围
    if batch_index is not None and batch_original_texts is not None:
        # 并发模式：使用已完成的页面 + 当前批次中已处理的页面
        available_pages = all_page_translations.copy()

        # 添加当前批次中在当前页面之前的页面
        for i in range(batch_index):
            if i < len(batch_original_texts) and batch_original_texts[i]:
                # 在并发模式下，我们使用原文作为"已完成"的页面
                if use_original_text:
                    available_pages.append(batch_original_texts[i])
                else:
                    # 如果不使用原文，则跳过当前批次的页面（因为它们还没有翻译完成）
                    pass
    elif current_page_index is not None:
        # 使用指定页面索引之前的页面作为上下文
        available_pages = all_page_translations[:current_page_index] if all_page_translations else []
    else:
        # 使用所有已完成的页面
        available_pages = all_page_translations or []

    if not available_pages:
        return ""

    # 筛选出有句子的页面
    non_empty_pages = [
        page for page in available_pages
        if any(sent.strip() for sent in page.values())
    ]
    # 实际要用的页数
    pages_used = min(context_size, len(non_empty_pages))
    if pages_used == 0:
        return ""
    tail = non_empty_pages[-pages_used:]

    # 拼接 - 根据参数决定使用原文还是译文
    lines = []
    for page in tail:
        for sent in page.values():
            if sent.strip():
                lines.append(sent.strip())

    # 如果使用原文，需要从原始数据中获取
    if use_original_text and has_original_page_texts:
        # 尝试获取对应的原文
        original_lines = []
        for i, page in enumerate(tail):
            page_idx = available_pages.index(page)
            if page_idx < len(original_page_texts):
                original_page = original_page_texts[page_idx]
                for sent in original_page.values():
                    if sent.strip():
                        original_lines.append(sent.strip())
        if original_lines:
            lines = original_lines

    numbered = [f"<|{i+1}|>{s}" for i, s in enumerate(lines)]
    context_type = "original text" if use_original_text else "translation results"
    return f"Here are the previous {context_type} for reference:\n" + "\n".join(numbered)
