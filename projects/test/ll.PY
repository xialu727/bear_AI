import pygame
import random
import sys

# 初始化pygame
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
        self.length = 3
        self.positions = [(GRID_WIDTH // 2, GRID_HEIGHT // 2)]
        self.direction = RIGHT
        self.score = 0
        self.grow_pending = 2  # 初始长度为3，所以需要再生长2节
        
    def get_head_position(self):
        return self.positions[0]
    
    def turn(self, point):
        # 防止直接反向移动
        if self.length > 1 and (point[0] * -1, point[1] * -1) == self.direction:
            return
        self.direction = point
    
    def move(self):
        head = self.get_head_position()
        x, y = self.direction
        new_x = (head[0] + x) % GRID_WIDTH
        new_y = (head[1] + y) % GRID_HEIGHT
        new_position = (new_x, new_y)
        
        # 检查是否撞到自己
        if new_position in self.positions[1:]:
            self.reset()
            return False
            
        self.positions.insert(0, new_position)
        
        if self.grow_pending > 0:
            self.grow_pending -= 1
        else:
            self.positions.pop()
            
        return True
    
    def grow(self):
        self.grow_pending += 1
        self.length += 1
        self.score += 10
    
    def draw(self, surface):
        for i, p in enumerate(self.positions):
            # 蛇头用不同颜色
            color = BLUE if i == 0 else GREEN
            
            # 绘制蛇身矩形
            rect = pygame.Rect(p[0] * GRID_SIZE, p[1] * GRID_SIZE, GRID_SIZE, GRID_SIZE)
            pygame.draw.rect(surface, color, rect)
            pygame.draw.rect(surface, BLACK, rect, 1)
            
            # 绘制蛇眼睛（只在头部）
            if i == 0:
                eye_size = GRID_SIZE // 5
                # 根据方向确定眼睛位置
                if self.direction == RIGHT:
                    left_eye = (p[0] * GRID_SIZE + GRID_SIZE - eye_size*2, p[1] * GRID_SIZE + eye_size*2)
                    right_eye = (p[0] * GRID_SIZE + GRID_SIZE - eye_size*2, p[1] * GRID_SIZE + GRID_SIZE - eye_size*3)
                elif self.direction == LEFT:
                    left_eye = (p[0] * GRID_SIZE + eye_size, p[1] * GRID_SIZE + eye_size*2)
                    right_eye = (p[0] * GRID_SIZE + eye_size, p[1] * GRID_SIZE + GRID_SIZE - eye_size*3)
                elif self.direction == UP:
                    left_eye = (p[0] * GRID_SIZE + eye_size*2, p[1] * GRID_SIZE + eye_size)
                    right_eye = (p[0] * GRID_SIZE + GRID_SIZE - eye_size*3, p[1] * GRID_SIZE + eye_size)
                else:  # DOWN
                    left_eye = (p[0] * GRID_SIZE + eye_size*2, p[1] * GRID_SIZE + GRID_SIZE - eye_size*2)
                    right_eye = (p[0] * GRID_SIZE + GRID_SIZE - eye_size*3, p[1] * GRID_SIZE + GRID_SIZE - eye_size*2)
                
                pygame.draw.circle(surface, WHITE, left_eye, eye_size)
                pygame.draw.circle(surface, WHITE, right_eye, eye_size)
                pygame.draw.circle(surface, BLACK, left_eye, eye_size//2)
                pygame.draw.circle(surface, BLACK, right_eye, eye_size//2)

class Food:
    def __init__(self):
        self.position = (0, 0)
        self.color = RED
        self.randomize_position()
    
    def randomize_position(self):
        self.position = (random.randint(0, GRID_WIDTH - 1), random.randint(0, GRID_HEIGHT - 1))
    
    def draw(self, surface):
        rect = pygame.Rect(self.position[0] * GRID_SIZE, self.position[1] * GRID_SIZE, GRID_SIZE, GRID_SIZE)
        pygame.draw.rect(surface, self.color, rect)
        pygame.draw.rect(surface, BLACK, rect, 1)
        
        # 绘制食物内部的细节
        inner_rect = pygame.Rect(
            self.position[0] * GRID_SIZE + GRID_SIZE//4,
            self.position[1] * GRID_SIZE + GRID_SIZE//4,
            GRID_SIZE//2,
            GRID_SIZE//2
        )
        pygame.draw.rect(surface, (255, 200, 200), inner_rect, border_radius=3)

class Game:
    def __init__(self):
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("贪吃蛇小游戏")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont('simhei', 25)
        self.big_font = pygame.font.SysFont('simhei', 50)
        self.snake = Snake()
        self.food = Food()
        self.game_over = False
        self.speed = FPS
        
    def draw_grid(self):
        for x in range(0, WIDTH, GRID_SIZE):
            pygame.draw.line(self.screen, GRAY, (x, 0), (x, HEIGHT), 1)
        for y in range(0, HEIGHT, GRID_SIZE):
            pygame.draw.line(self.screen, GRAY, (0, y), (WIDTH, y), 1)
    
    def draw_score(self):
        score_text = self.font.render(f'得分: {self.snake.score}', True, WHITE)
        length_text = self.font.render(f'长度: {self.snake.length}', True, WHITE)
        self.screen.blit(score_text, (10, 10))
        self.screen.blit(length_text, (10, 40))
        
        # 绘制操作说明
        controls = [
            "方向键: 控制移动",
            "空格键: 暂停/继续",
            "R键: 重新开始",
            "ESC键: 退出游戏"
        ]
        
        for i, text in enumerate(controls):
            control_text = self.font.render(text, True, WHITE)
            self.screen.blit(control_text, (WIDTH - 250, 10 + i * 30))
    
    def draw_game_over(self):
        game_over_text = self.big_font.render('游戏结束!', True, RED)
        score_text = self.font.render(f'最终得分: {self.snake.score}', True, WHITE)
        restart_text = self.font.render('按R键重新开始', True, GREEN)
        
        self.screen.blit(game_over_text, (WIDTH//2 - game_over_text.get_width()//2, HEIGHT//2 - 60))
        self.screen.blit(score_text, (WIDTH//2 - score_text.get_width()//2, HEIGHT//2))
        self.screen.blit(restart_text, (WIDTH//2 - restart_text.get_width()//2, HEIGHT//2 + 40))
    
    def check_food_collision(self):
        if self.snake.get_head_position() == self.food.position:
            self.snake.grow()
            self.food.randomize_position()
            
            # 确保食物不出现在蛇身上
            while self.food.position in self.snake.positions:
                self.food.randomize_position()
            
            # 每得100分增加一点速度
            if self.snake.score % 100 == 0:
                self.speed += 1
    
    def run(self):
        paused = False
        
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        pygame.quit()
                        sys.exit()
                    
                    if event.key == pygame.K_r:
                        self.snake.reset()
                        self.food.randomize_position()
                        self.game_over = False
                        self.speed = FPS
                    
                    if event.key == pygame.K_SPACE:
                        paused = not paused
                    
                    if not paused and not self.game_over:
                        if event.key == pygame.K_UP:
                            self.snake.turn(UP)
                        elif event.key == pygame.K_DOWN:
                            self.snake.turn(DOWN)
                        elif event.key == pygame.K_LEFT:
                            self.snake.turn(LEFT)
                        elif event.key == pygame.K_RIGHT:
                            self.snake.turn(RIGHT)
            
            if not paused and not self.game_over:
                # 移动蛇
                if not self.snake.move():
                    self.game_over = True
                
                # 检查食物碰撞
                self.check_food_collision()
            
            # 绘制
            self.screen.fill(BLACK)
            self.draw_grid()
            self.snake.draw(self.screen)
            self.food.draw(self.screen)
            self.draw_score()
            
            if self.game_over:
                self.draw_game_over()
            
            if paused:
                pause_text = self.big_font.render('游戏暂停', True, BLUE)
                self.screen.blit(pause_text, (WIDTH//2 - pause_text.get_width()//2, HEIGHT//2))
                continue_text = self.font.render('按空格键继续', True, WHITE)
                self.screen.blit(continue_text, (WIDTH//2 - continue_text.get_width()//2, HEIGHT//2 + 60))
            
            pygame.display.flip()
            self.clock.tick(self.speed)

if __name__ == "__main__":
    game = Game()
    game.run()
