import json
import os

def load_json(filename):
    with open(filename, encoding="utf-8") as f:
        return json.load(f)

def build_category_tree(categories):
    # Создаем словарь: id -> категория
    cat_by_id = {cat["id"]: cat for cat in categories}
    # Добавляем для каждой категории поле children
    for cat in categories:
        cat["children"] = []
    # Строим дерево: для каждой категории, если есть родитель, добавляем её в children родителя
    tree = []
    for cat in categories:
        parent_id = cat.get("parent")
        if parent_id is None:
            tree.append(cat)
        else:
            parent = cat_by_id.get(parent_id)
            if parent:
                parent["children"].append(cat)
    return tree

def assign_services_to_categories(category_tree, services):
    # Для каждого узла дерева добавляем список услуг, где service["category"] совпадает с узлом (например, по имени)
    for node in category_tree:
        # Здесь предположим, что поле service["category"] содержит имя категории
        node["services"] = [s for s in services if s["category"] == node["name"]]
        if node["children"]:
            assign_services_to_categories(node["children"], services)

if __name__ == "__main__":
    # Пути к файлам JSON (у вас они могут находиться в static/data или другом каталоге)
    services_file = os.path.join(os.path.dirname(__file__), "static", "data", "services.json")
    categories_file = os.path.join(os.path.dirname(__file__), "static", "data", "categories.json")

    services = load_json(services_file)
    categories = load_json(categories_file)

    # Фильтруем категории – оставляем только те, что имеют parent == null (главные)
    # Если же вам нужно построить полное дерево, то сначала строим дерево, а затем для отображения на сайте отбираем главные узлы
    category_tree = build_category_tree(categories)
    # В category_tree теперь будут только главные категории с вложенными подкатегориями

    # Связываем услуги с категориями
    assign_services_to_categories(category_tree, services)

    # Для проверки можно вывести дерево (например, сохранить результат в JSON)
    output_file = os.path.join(os.path.dirname(__file__), "static", "data", "categories_tree.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(category_tree, f, ensure_ascii=False, indent=4)
    print(f"Дерево категорий с услугами сохранено в {output_file}")
