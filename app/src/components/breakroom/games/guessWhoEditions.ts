// Data-driven Guess Who editions. Adding a new edition = append one entry here;
// nothing else needs to change. The Food edition ships first.
//
// Each attribute key has a plain yes/no question the computer can ask. An item's
// `attrs` lists only the attributes that are TRUE for it (missing = false), so a
// 9-year-old answering "is it sweet?" about their own card always matches.

export interface GwItem {
  id: string
  name: string
  emoji: string
  attrs: Record<string, boolean>
}

export interface GwEdition {
  id: string
  name: string
  /** attribute key -> the yes/no question the computer asks for it. */
  questions: Record<string, string>
  items: GwItem[]
}

const FOOD_QUESTIONS: Record<string, string> = {
  fruit: 'Is your food a fruit?',
  vegetable: 'Is your food a vegetable?',
  sweet: 'Is your food sweet?',
  drink: 'Do you drink your food instead of eating it?',
  dairy: 'Is your food made from milk?',
  meat: 'Does your food have meat in it?',
  hot: 'Is your food usually eaten warm or hot?',
  red: 'Is your food red?',
  green: 'Is your food green?',
  round: 'Is your food round?',
  dessert: 'Is your food a dessert or a treat?',
  breakfast: 'Is your food often eaten at breakfast?',
  crunchy: 'Is your food crunchy?',
  fromWater: 'Does your food come from the water?',
}

function item(id: string, name: string, emoji: string, attrs: string[]): GwItem {
  const map: Record<string, boolean> = {}
  for (const a of attrs) map[a] = true
  return { id, name, emoji, attrs: map }
}

const FOOD_ITEMS: GwItem[] = [
  item('apple', 'Apple', '🍎', ['fruit', 'sweet', 'red', 'round', 'crunchy']),
  item('banana', 'Banana', '🍌', ['fruit', 'sweet']),
  item('grapes', 'Grapes', '🍇', ['fruit', 'sweet', 'round']),
  item('strawberry', 'Strawberry', '🍓', ['fruit', 'sweet', 'red']),
  item('watermelon', 'Watermelon', '🍉', ['fruit', 'sweet', 'red', 'round']),
  item('lemon', 'Lemon', '🍋', ['fruit', 'round']),
  item('carrot', 'Carrot', '🥕', ['vegetable', 'crunchy']),
  item('broccoli', 'Broccoli', '🥦', ['vegetable', 'green', 'hot']),
  item('corn', 'Corn', '🌽', ['vegetable', 'hot']),
  item('salad', 'Salad', '🥗', ['vegetable', 'green']),
  item('pizza', 'Pizza', '🍕', ['hot', 'dairy']),
  item('hamburger', 'Hamburger', '🍔', ['hot', 'meat', 'round']),
  item('taco', 'Taco', '🌮', ['hot', 'meat', 'crunchy']),
  item('chicken', 'Chicken', '🍗', ['hot', 'meat']),
  item('fish', 'Fish', '🐟', ['hot', 'meat', 'fromWater']),
  item('egg', 'Egg', '🥚', ['hot', 'breakfast', 'round']),
  item('bread', 'Bread', '🍞', ['breakfast']),
  item('cheese', 'Cheese', '🧀', ['dairy']),
  item('milk', 'Milk', '🥛', ['drink', 'dairy']),
  item('coffee', 'Coffee', '☕', ['drink', 'hot']),
  item('icecream', 'Ice cream', '🍦', ['sweet', 'dessert', 'dairy']),
  item('cake', 'Cake', '🍰', ['sweet', 'dessert']),
  item('donut', 'Donut', '🍩', ['sweet', 'dessert', 'round', 'breakfast']),
  item('cookie', 'Cookie', '🍪', ['sweet', 'dessert', 'round', 'crunchy']),
]

export const EDITIONS: GwEdition[] = [
  {
    id: 'food',
    name: 'Food',
    questions: FOOD_QUESTIONS,
    items: FOOD_ITEMS,
  },
]
