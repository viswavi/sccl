"""
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved

Author: Dejiao Zhang (dejiaoz@amazon.com)
Date: 02/26/2021
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import Parameter
from transformers import BertPreTrainedModel
# from transformers import AutoModel, AutoTokenizer

class SCCLBert(nn.Module):
    def __init__(self, bert_model, tokenizer, cluster_centers=None, alpha=1.0):
        super(SCCLBert, self).__init__()
        
        self.tokenizer = tokenizer
        self.bert = bert_model
        self.emb_size = self.bert.config.hidden_size
        self.alpha = alpha
        
        # Instance-CL head
        self.contrast_head = nn.Sequential(
            nn.Linear(self.emb_size, self.emb_size),
            nn.ReLU(inplace=True),
            nn.Linear(self.emb_size, 128))
        
        # Clustering head
        initial_cluster_centers = torch.tensor(
            cluster_centers, dtype=torch.float, requires_grad=True)
        self.cluster_centers = Parameter(initial_cluster_centers)
      
    
    def forward(self, input_ids, attention_mask, task_type="virtual"):
        if task_type == "evaluate":
            return self.get_mean_embeddings(input_ids, attention_mask)
        
        elif task_type == "virtual":
            input_ids_1, input_ids_2 = torch.unbind(input_ids, dim=1)
            attention_mask_1, attention_mask_2 = torch.unbind(attention_mask, dim=1) 
            
            mean_output_1 = self.get_mean_embeddings(input_ids_1, attention_mask_1)
            mean_output_2 = self.get_mean_embeddings(input_ids_2, attention_mask_2)

            return mean_output_1, mean_output_2
        
        elif task_type == "explicit":
            input_ids_1, input_ids_2, input_ids_3 = torch.unbind(input_ids, dim=1)
            attention_mask_1, attention_mask_2, attention_mask_3 = torch.unbind(attention_mask, dim=1) 
            
            mean_output_1 = self.get_mean_embeddings(input_ids_1, attention_mask_1)
            mean_output_2 = self.get_mean_embeddings(input_ids_2, attention_mask_2)
            mean_output_3 = self.get_mean_embeddings(input_ids_3, attention_mask_3)
            return mean_output_1, mean_output_2, mean_output_3
        
        else:
            raise Exception("TRANSFORMER ENCODING TYPE ERROR! OPTIONS: [EVALUATE, VIRTUAL, EXPLICIT]")
      
    
    def get_mean_embeddings(self, input_ids, attention_mask):
        bert_output = self.bert.forward(input_ids=input_ids, attention_mask=attention_mask)
        attention_mask = attention_mask.unsqueeze(-1)
        mean_output = torch.sum(bert_output[0]*attention_mask, dim=1) / torch.sum(attention_mask, dim=1)
        return mean_output
    

    def get_cluster_prob(self, embeddings):
        norm_squared = torch.sum((embeddings.unsqueeze(1) - self.cluster_centers) ** 2, 2)
        numerator = 1.0 / (1.0 + (norm_squared / self.alpha))
        power = float(self.alpha + 1) / 2
        numerator = numerator ** power
        return numerator / torch.sum(numerator, dim=1, keepdim=True)

    def local_consistency(self, embd0, embd1, embd2, criterion):
        p0 = self.get_cluster_prob(embd0)
        p1 = self.get_cluster_prob(embd1)
        p2 = self.get_cluster_prob(embd2)
        
        lds1 = criterion(p1, p0)
        lds2 = criterion(p2, p0)
        return lds1+lds2
    
    def contrast_logits(self, embd1, embd2=None):
        feat1 = F.normalize(self.contrast_head(embd1), dim=1)
        if embd2 != None:
            feat2 = F.normalize(self.contrast_head(embd2), dim=1)
            return feat1, feat2
        else: 
            return feat1


class SCCLMatrix(nn.Module):
    def __init__(self, emb_size, cluster_centers=None, alpha=1.0, include_contrastive_loss=False, linear_transformation=True):
        super(SCCLMatrix, self).__init__()
        
        self.emb_size = emb_size

        '''
        self.linear_matrix = nn.Linear(self.emb_size, self.emb_size)
        self.linear_matrix.weight.data.copy_(torch.eye(self.emb_size))

        '''
        self.linear_matrix = nn.Sequential(
                nn.Linear(self.emb_size, 128),
                nn.ReLU(inplace=True),
                nn.Linear(128, self.emb_size))


        self.alpha = alpha
        self.include_contrastive_loss = include_contrastive_loss

        # Instance-CL head
        if self.include_contrastive_loss:
            self.contrast_head = nn.Sequential(
                nn.Linear(self.emb_size, self.emb_size),
                nn.ReLU(inplace=True),
                nn.Linear(self.emb_size, 128))
        
        # Clustering head
        initial_cluster_centers = torch.tensor(
            cluster_centers, dtype=torch.float, requires_grad=True)
        self.cluster_centers = Parameter(initial_cluster_centers)

        self.linear_transformation = linear_transformation
    
    def forward(self, points, task_type):
        # TODO(vijay): add noise

        if task_type == "evaluate":
            if self.linear_transformation:
                return self.linear_matrix(points)
            else:
                return points
        
        elif task_type == "virtual":
            points1 = points
            with torch.no_grad():
                unit_gaussian_noise = torch.randn(points.shape, device=points.device)
                std = torch.pow(torch.var(points, dim=0), 0.5) / 8
                scaled_noise = unit_gaussian_noise * std
                points2 = points + scaled_noise
            if self.linear_transformation:
                transformed_points_1 = self.linear_matrix(points1)
                transformed_points_2 = self.linear_matrix(points2)
                return transformed_points_1, transformed_points_2
            else:
                return points1, points2
        
        else:
            raise NotImplementedError


    def get_cluster_prob(self, embeddings):
        norm_squared = torch.sum((embeddings.unsqueeze(1) - self.cluster_centers) ** 2, 2)
        numerator = 1.0 / (1.0 + (norm_squared / self.alpha))
        power = float(self.alpha + 1) / 2
        numerator = numerator ** power
        return numerator / torch.sum(numerator, dim=1, keepdim=True)


    def contrast_logits(self, embd1, embd2=None):
        assert self.include_contrastive_loss, "Contrastive head is not enabled for this model"
        feat1 = F.normalize(self.contrast_head(embd1), dim=1)
        if embd2 != None:
            feat2 = F.normalize(self.contrast_head(embd2), dim=1)
            return feat1, feat2
        else: 
            return feat1


class SCCLBertTransE(nn.Module):
    def __init__(self, X, bert_model, kge_model, emb_size, cluster_centers=None, alpha=1.0, include_contrastive_loss=False, linear_transformation=True, canonicalization_side_information=None):
        super(SCCLBertTransE, self).__init__()
        
        self.emb_size = emb_size

        '''
        self.linear_matrix = nn.Linear(self.emb_size, self.emb_size)
        self.linear_matrix.weight.data.copy_(torch.eye(self.emb_size))

        '''
        self.bert = bert_model
        self.kge = kge_model
        self.entity_embedding_matrix = torch.nn.Parameter(kge_model.entity_embedding[:len(X), :])


        self.alpha = alpha
        self.include_contrastive_loss = include_contrastive_loss

        # Instance-CL head
        if self.include_contrastive_loss:
            self.contrast_head = nn.Sequential(
                nn.Linear(self.emb_size, self.emb_size),
                nn.ReLU(inplace=True),
                nn.Linear(self.emb_size, 128))
        
        # Clustering head
        initial_cluster_centers = torch.tensor(
            cluster_centers, dtype=torch.float, requires_grad=True)
        self.cluster_centers = Parameter(initial_cluster_centers)
        self.canonicalization_side_information = canonicalization_side_information
    
    def forward(self, orig_ent_ids, noised_ent_ids, orig_text, noised_text, task_type):
        if task_type == "evaluate":
            orig_kge_embeddings = torch.matmul(orig_ent_ids, self.entity_embedding_matrix)
            orig_cls_token_embeddings, _ = self.bert(orig_text)
            orig_embeddings = torch.cat((orig_kge_embeddings, orig_cls_token_embeddings), dim=1)
            return orig_embeddings
        elif task_type == "explicit":
            orig_kge_embeddings = torch.matmul(orig_ent_ids, self.entity_embedding_matrix)
            orig_cls_token_embeddings, _ = self.bert(orig_text)
            orig_embeddings = torch.cat((orig_kge_embeddings, orig_cls_token_embeddings), dim=1)

            noised_kge_embeddings = torch.matmul(noised_ent_ids, self.entity_embedding_matrix)
            noised_cls_token_embeddings, _ = self.bert(noised_text)
            noised_embeddings = torch.cat((noised_kge_embeddings, noised_cls_token_embeddings), dim=1)

            return orig_embeddings, noised_embeddings
        else:
            raise NotImplementedError


    def get_cluster_prob(self, embeddings):
        norm_squared = torch.sum((embeddings.unsqueeze(1) - self.cluster_centers) ** 2, 2)
        numerator = 1.0 / (1.0 + (norm_squared / self.alpha))
        power = float(self.alpha + 1) / 2
        numerator = numerator ** power
        return numerator / torch.sum(numerator, dim=1, keepdim=True)


    def contrast_logits(self, embd1, embd2=None):
        assert self.include_contrastive_loss, "Contrastive head is not enabled for this model"
        feat1 = F.normalize(self.contrast_head(embd1), dim=1)
        if embd2 != None:
            feat2 = F.normalize(self.contrast_head(embd2), dim=1)
            return feat1, feat2
        else: 
            return feat1


