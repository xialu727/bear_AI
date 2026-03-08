import pygame
import random
import sys

# 初始化 Pygame
pygame.init()

# 游戏常量
WIDTH, HEIGHT = 600, 600
GRID_SIZE = 20
GRID_WIDTH = WIDTH // GRID_SIZE
GRID_HEIGHT = HEIGHT // GRID_SIZE
FPS = 10

# 颜色定义
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GREEN = (0, 255, 0)
RED = (255, 0, 0)
BLUE = (0, 120, 255)
GRAY = (40, 40, 40)

# 方向常量
UP = (0, -1)
DOWN = (0, 1)
LEFT = (-1, 0)
RIGHT = (1, 0)

class Snake:
    def __init__(self):
        self.reset()
        
    def reset(self):
        # 蛇初始位置在屏幕中央
        self.length = 3
        self.positions = [(GRID_WIDTH // 2, GRID_HEIGHT // 2)]
        # 初始方向向右
        self.direction = RIGHT
        self.score = 0
        self.grow_pending = 2  # 初始长度为3，所以还需要增长2次
        
    def get_head_position(self):
        return self.positions[0]
    
    def turn(self, new_direction):
        # 防止直接反向移动（例如不能从向右直接变为向左）
        if (new_direction[0] * -1, new_direction[1] * -1) != self.direction:
            self.direction = new_direction
    
    def move(self):
        head = self.get_head_position()
        new_x = (head[0] + self.direction[0]) % GRID_WIDTH
        new_y = (head[1] + self.direction[1]) % GRID_HEIGHT
        new_position = (new_x, new_y)
        
        # 检查是否撞到自己
        if new_position in self.positions[1:]:
            return False  # 游戏结束
        
        self.positions.insert(0, new_position)
        
        if self.grow_pending > 0:
            self.grow_pending -= 1
        else:
            self.positions.pop()
            
        return True  # 游戏继续
    
    def grow(self):
        self.grow_pending += 1
        self.score += 10
        self.length += 1
    
    def draw(self, surface):
        for i, p in enumerate(self.positions):
            # 蛇头用不同颜色
            color = BLUE if i == 0 else GREEN
            rect = pygame.Rect(p[0] * GRID_SIZE, p[1] * GRID_SIZE, GRID_SIZE, GRID_SIZE)
            pygame.draw.rect(surface, color, rect)
            pygame.draw.rect(surface, BLACK, rect, 1)  # 黑色边框
            
            # 为蛇头画眼睛
            if i == 0:
                eye_size = GRID_SIZE // 5
                # 根据方向确定眼睛位置
                if self.direction == RIGHT:
                    eye1_pos = (p[0] * GRID_SIZE + GRID_SIZE - eye_size - 2, p[1] * GRID_SIZE + 5)
                    eye2_pos = (p[0] * GRID_SIZE + GRID_SIZE - eye_size - 2, p[1] * GRID_SIZE + GRID_SIZE - 5 - eye_size)
                elif self.direction == LEFT:
                    eye1_pos = (p[0] * GRID_SIZE + 2, p[1] * GRID_SIZE + 5)
                    eye2_pos = (p[0] * GRID_SIZE + 2, p[1] * GRID_SIZE + GRID_SIZE - 5 - eye_size)
                elif self.direction == UP:
                    eye1_pos = (p[0] * GRID_SIZE + 5, p[1] * GRID_SIZE + 2)
                    eye2_pos = (p[0] * GRID_SIZE + GRID_SIZE - 5 - eye_size, p[1] * GRID_SIZE + 2)
                else:  # DOWN
                    eye1_pos = (p[0] * GRID_SIZE + 5, p[1] * GRID_SIZE + GRID_SIZE - eye_size - 2)
                    eye2_pos = (p[0] * GRID_SIZE + GRID_SIZE - 5 - eye_size, p[1] * GRID_SIZE + GRID_SIZE - eye_size - 2)
                
                pygame.draw.rect(surface, WHITE, (eye1_pos[0], eye1_pos[1], eye_size, eye_size))
                pygame.draw.rect(surface, WHITE, (eye2_pos[0], eye2_pos[1], eye_size, eye_size))

class Food:
    def __init__(self):
        self.position = (0, 0)
        self.randomize_position()
    
    def randomize_position(self):
        self.position = (random.randint(0, GRID_WIDTH - 1), random.randint(0, GRID_HEIGHT - 1))
    
    def draw(self, surface):
        rect = pygame.Rect(self.position[0] * GRID_SIZE, self.position[1] * GRID_SIZE, GRID_SIZE, GRID_SIZE)
        pygame.draw.rect(surface, RED, rect)
        pygame.draw.rect(surface, BLACK, rect, 1)  # 黑色边框
        
        # 画一个苹果梗
        stem_rect = pygame.Rect(self.position[0] * GRID_SIZE + GRID_SIZE // 2 - 1, 
                               self.position[1] * GRID_SIZE - 3, 2, 4)
        pygame.draw.rect(surface, (139, 69, 19), stem_rect)

class Game:
    def __init__(self):
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("贪吃蛇小游戏")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 36)
        self.small_font = pygame.font.SysFont(None, 24)
        
        self.snake = Snake()
        self.food = Food()
        self.game_over = False
        self.game_started = False
    
    def draw_grid(self):
        for x in range(0, WIDTH, GRID_SIZE):
            pygame.draw.line(self.screen, GRAY, (x, 0), (x, HEIGHT), 1)
        for y in range(0, HEIGHT, GRID_SIZE):
            pygame.draw.line(self.screen, GRAY, (0, y), (WIDTH, y), 1)
    
    def draw_score(self):
        score_text = self.font.render(f"得分: {self.snake.score}", True, WHITE)
        length_text = self.small_font.render(f"长度: {self.snake.length}", True, WHITE)
        self.screen.blit(score_text, (10, 10))
        self.screen.blit(length_text, (10, 50))
    
    def draw_game_over(self):
        game_over_font = pygame.font.SysFont(None, 72)
        game_over_text = game_over_font.render("游戏结束!", True, RED)
        restart_text = self.font.render("按R键重新开始", True, WHITE)
        quit_text = self.small_font.render("按Q键退出游戏", True, WHITE)
        
        self.screen.blit(game_over_text, (WIDTH // 2 - game_over_text.get_width() // 2, HEIGHT // 2 - 50))
        self.screen.blit(restart_text, (WIDTH // 2 - restart_text.get_width() // 2, HEIGHT // 2 + 30))
        self.screen.blit(quit_text, (WIDTH // 2 - quit_text.get_width() // 2, HEIGHT // 2 + 70))
    
    def draw_start_screen(self):
        title_font = pygame.font.SysFont(None, 72)
        title_text = title_font.render("贪吃蛇游戏", True, GREEN)
        start_text = self.font.render("按空格键开始游戏", True, WHITE)
        controls_text1 = self.small_font.render("使用方向键或WASD控制蛇的移动", True, WHITE)
        controls_text2 = self.small_font.render("按P键暂停游戏", True, WHITE)
        
        self.screen.blit(title_text, (WIDTH // 2 - title_text.get_width() // 2, HEIGHT // 2 - 100))
        self.screen.blit(start_text, (WIDTH // 2 - start_text.get_width() // 2, HEIGHT // 2))
        self.screen.blit(controls_text1, (WIDTH // 2 - controls_text1.get_width() // 2, HEIGHT // 2 + 60))
        self.screen.blit(controls_text2, (WIDTH // 2 - controls_text2.get_width() // 2, HEIGHT // 2 + 90))
    
    def run(self):
        paused = False
        
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                
                if event.type == pygame.KEYDOWN:
                    # 开始屏幕控制
                    if not self.game_started:
                        if event.key == pygame.K_SPACE:
                            self.game_started = True
                    
                    # 游戏控制
                    elif not self.game_over:
                        if event.key == pygame.K_p:
                            paused = not paused
                        
                        if not paused:
                            # 方向键控制
                            if event.key == pygame.K_UP:
                                self.snake.turn(UP)
                            elif event.key == pygame.K_DOWN:
                                self.snake.turn(DOWN)
                            elif event.key == pygame.K_LEFT:
                                self.snake.turn(LEFT)
                            elif event.key == pygame.K_RIGHT:
                                self.snake.turn(RIGHT)
                            # WASD控制
                            elif event.key == pygame.K_w:
                                self.snake.turn(UP)
                            elif event.key == pygame.K_s:
                                self.snake.turn(DOWN)
                            elif event.key == pygame.K_a:
                                self.snake.turn(LEFT)
                            elif event.key == pygame.K_d:
                                self.snake.turn(RIGHT)
                    
                    # 游戏结束控制
                    if self.game_over:
                        if event.key == pygame.K_r:
                            self.snake.reset()
                            self.food.randomize_position()
                            self.game_over = False
                            paused = False
                        elif event.key == pygame.K_q:
                            pygame.quit()
                            sys.exit()
            
            # 填充背景色
            self.screen.fill(BLACK)
            
            # 绘制网格
            self.draw_grid()
            
            if not self.game_started:
                # 显示开始屏幕
                self.draw_start_screen()
            elif self.game_over:
                # 显示游戏结束屏幕
                self.snake.draw(self.screen)
                self.food.draw(self.screen)
                self.draw_score()
                self.draw_game_over()
            elif paused:
                # 显示暂停状态
                self.snake.draw(self.screen)
                self.food.draw(self.screen)
                self.draw_score()
                
                pause_text = self.font.render("游戏暂停 - 按P键继续", True, WHITE)
                self.screen.blit(pause_text, (WIDTH // 2 - pause_text.get_width() // 2, HEIGHT // 2))
            else:
                # 游戏逻辑
                if not self.snake.move():
                    self.game_over = True
                
                # 检查是否吃到食物
                if self.snake.get_head_position() == self.food.position:
                    self.snake.grow()
                    self.food.randomize_position()
                    # 确保食物不出现在蛇身上
                    while self.food.position in self.snake.positions:
                        self.food.randomize_position()
                
                # 绘制游戏元素
                self.snake.draw(self.screen)
                self.food.draw(self.screen)
                self.draw_score()
            
            # 更新屏幕
            pygame.display.flip()
            
            # 控制游戏速度
            if self.game_started and not self.game_over and not paused:
                self.clock.tick(FPS)
            else:
                self.clock.tick(30)  # 菜单和结束画面使用较低帧率

if __name__ == "__main__":
    game = Game()
    game.run()
